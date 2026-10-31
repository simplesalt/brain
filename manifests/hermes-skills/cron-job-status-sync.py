#!/usr/bin/env python3
"""Pick new job-application and recruiter-call emails for Hermes to match to the Notion Jobs database.

Runs hourly as the trigger for the job-status-sync cron job. It lists Dylan's email threads from gbrain
(source gmail) and keeps the ones with a new message that looks like it is about a job: mail from an
applicant tracking system, or a subject about an application, interview, offer or call with a recruiter.
It prints a batch for Hermes, who matches each thread to a row in Jobs, moves that row's Stage, and lists
what it cannot match on the "Unmatched job emails" Notion page. Nothing here emails Dylan.

State lives on the gbrain page ops/job-status-sync: the gbrain update time last examined for each thread,
so unchanged threads are not read again, and the message count last handed to Hermes, so a thread is
handed over again only when a new message lands in it. Hermes answers with JSON after a
JOB-STATUS-RESULT line; the next run copies those answers onto the same page as a log. With nothing to do
it prints {"wakeAgent": false} so the job skips the model call.
"""
import datetime
import json
import os
import re
import sys
import urllib.request

URL = os.environ.get("GBRAIN_URL", "http://gbrain:3131/mcp")
TOKEN = os.environ.get("GBRAIN_REMOTE_TOKEN", "gbrain-mcp")
STATE = "ops/job-status-sync"
BATCH = int(os.environ.get("JOB_STATUS_BATCH", "5"))
DAYS = 30
MAX_EXAMINED = 200
LOG_KEEP = 200
OUTPUTS_KEEP = 1000
OUTPUT_DIR = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "cron", "output")
MARKER = "JOB-STATUS-RESULT"
DRY_RUN = os.environ.get("JOB_STATUS_DRY_RUN") == "1"
SKIP_LABELS = {"SPAM"}
# Applicant tracking systems that send application, interview and decision mail for employers.
ATS = re.compile(r"@([a-z0-9-]+\.)*(greenhouse(-mail)?\.io|greenhouse\.com|lever\.co|ashbyhq\.com|"
                 r"myworkday(jobs)?\.com|workday\.com|smartrecruiters\.com|icims\.com|jobvite\.com|bamboohr\.com|"
                 r"workable(mail)?\.com|recruitee\.com|breezy\.hr|breezyhr\.com|rippling\.com|"
                 r"teamtailor(-mail)?\.com|dover\.(com|io)|successfactors\.(com|eu)|taleo\.net|oraclecloud\.com|"
                 r"paylocity\.com|adp\.com|ultipro\.com|ukg\.com|jazzhr\.com|applytojob\.com|pinpointhq\.com|"
                 r"gem\.com|wellfound\.com|hiringthing\.com|jobscore\.com|comeet\.(co|com)|personio\.(de|com))$",
                 re.I)
SUBJECT = re.compile(r"(your application|application (received|submitted|update|status)|thank(s| you) for "
                     r"(applying|your (interest|application))|candidacy|interview|phone screen|screening call|"
                     r"recruit|next steps|offer letter|job offer|hiring (team|manager)|"
                     r"(intro|introductory|quick) (call|chat)|schedule (a )?(call|time|chat)|"
                     r"position|role at|opportunity at)", re.I)


def call(tool, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=60) as r:
        for line in r.read().decode().splitlines():
            if line.startswith("data: "):
                msg = json.loads(line[6:])
                if "error" in msg:
                    raise RuntimeError(f"{tool}: {msg['error']}")
                text = msg["result"]["content"][0]["text"]
                if msg["result"].get("isError"):
                    raise RuntimeError(f"{tool}: {text[:300]}")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
    raise RuntimeError(f"{tool}: no response")


def all_pages(**filters):
    pages, offset = [], 0
    while True:
        chunk = call("list_pages", dict(filters, limit=100, offset=offset))
        if not isinstance(chunk, list) or not chunk:
            return pages
        pages += chunk
        offset += len(chunk)


def address(entry):
    m = re.search(r"<([^>]+)>", entry or "")
    return (m.group(1) if m else (entry or "")).strip().lower()


def is_job_email(fm, subject):
    senders = [address(a) for a in [fm.get("from")] + list(fm.get("senders") or [])]
    from_ats = any(ATS.search(a) for a in senders if a)
    return from_ats, from_ats or bool(SUBJECT.search(subject))


def load_state():
    state = {}
    try:
        page = call("get_page", {"slug": STATE})
        body = (page.get("compiled_truth") or "") if isinstance(page, dict) else ""
        m = re.search(r"```json\n(.*?)\n```", body, re.S)
        state = json.loads(m.group(1)) if m else {}
    except RuntimeError:
        pass
    return {"seen": state.get("seen") or {}, "handed": state.get("handed") or {},
            "outputs": state.get("outputs") or [], "log": state.get("log") or []}


def save_state(state):
    state["log"] = state["log"][-LOG_KEEP:]
    content = ("---\ntype: note\ntitle: Job status sync state\n---\n\n# Job status sync state\n\n"
               "Email threads the job-status-sync job has examined and handed to Hermes, and what Hermes did "
               "with each (updated, unchanged, unmatched or not a job email). Kept by cron-job-status-sync.py.\n\n"
               "```json\n" + json.dumps(state, indent=1, sort_keys=True) + "\n```\n")
    call("put_page", {"slug": STATE, "content": content})


def read_results(read):
    """Collect Hermes's answers from saved job output not read yet; returns (output keys, log entries)."""
    keys, entries = [], []
    try:
        jobs = os.listdir(OUTPUT_DIR)
    except OSError:
        return keys, entries
    for job in jobs:
        d = os.path.join(OUTPUT_DIR, job)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            key = f"{job}/{f}"
            if key in read or not f.endswith(".md"):
                continue
            text = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
            if MARKER not in text:
                continue
            m = re.search(r"\[.*\]", text.split(MARKER, 1)[1], re.S)
            try:
                answer = json.loads(m.group(0)) if m else []
            except json.JSONDecodeError as e:
                print(f"job-status-sync: unreadable answer in {key}: {e}", file=sys.stderr)
                answer = []
            entries += [a for a in answer if isinstance(a, dict)] if isinstance(answer, list) else []
            keys.append(key)
    return keys, entries


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(days=DAYS)).date().isoformat()
    state = load_state()
    keys, entries = read_results(set(state["outputs"]))
    state["outputs"] += keys
    state["log"] += entries
    pages = [p for p in all_pages(type="email", source_id="gmail")
             if (p.get("updated_at") or "")[:10] >= since and state["seen"].get(p["slug"]) != p.get("updated_at")]
    pages.sort(key=lambda p: p.get("updated_at") or "")
    batch, examined = [], 0
    for page in pages:
        if len(batch) >= BATCH or examined >= MAX_EXAMINED:
            break
        examined += 1
        slug = page["slug"]
        state["seen"][slug] = page.get("updated_at")
        fm = (call("get_page", {"slug": slug}) or {}).get("frontmatter") or {}
        count = int(fm.get("message_count") or 1)
        if set(fm.get("labels") or []) & SKIP_LABELS or (fm.get("date") or "")[:10] < since:
            continue
        if count <= int(state["handed"].get(slug) or 0):
            continue
        subject = page.get("title") or ""
        from_ats, job = is_job_email(fm, subject)
        if not job:
            continue
        batch.append({"slug": slug, "date": (fm.get("date") or "")[:10], "subject": subject,
                      "from": fm.get("from"), "from_ats": from_ats, "messages": count,
                      "new_messages": count - int(state["handed"].get(slug) or 0)})
        state["handed"][slug] = count
    # Forget threads that have aged out of the window, so the state page stays small.
    state["seen"] = {s: u for s, u in state["seen"].items() if (u or "")[:10] >= since}
    state["handed"] = {s: n for s, n in state["handed"].items() if s in state["seen"]}
    state["outputs"] = state["outputs"][-OUTPUTS_KEEP:]
    if (examined or keys) and not DRY_RUN:
        save_state(state)
    if not batch:
        print(json.dumps({"wakeAgent": False}))
        return
    print(f"{len(batch)} email threads to match; {len(pages) - examined} changed threads not yet examined.")
    print(json.dumps(batch, indent=1))


if __name__ == "__main__":
    main()
