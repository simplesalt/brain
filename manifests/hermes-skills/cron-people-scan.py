#!/usr/bin/env python3
"""Pick the next meetings and email threads whose people gbrain has not met yet.

Runs every minute as the trigger for the people-scan cron job: it wakes Hermes only when gbrain has synced
a meeting or email thread whose people it has not met yet. It lists Dylan's synced meetings (past ones only) and email
threads from gbrain, drops the ones already handled, newsletters and automated mail, and prints a
batch for Hermes to turn into people pages. Handled slugs are kept on the gbrain page ops/people-scan
(Hermes cannot tag pages in the gmail and gcal sources), marked when handed out so no item is
processed twice. With nothing to do it prints {"wakeAgent": false} so the job skips the model call.

Hermes only judges: its answer lists the real people as JSON after a PEOPLE-SCAN-RESULT line. Before
picking the next batch, this script reads those answers from the job's saved output and writes the
pages itself (a new page, or a timeline entry on an existing one), so page format never depends on
the model.
"""
import datetime
import json
import os
import re
import sys
import urllib.request

URL = os.environ.get("GBRAIN_URL", "http://gbrain:3131/mcp")
TOKEN = os.environ.get("GBRAIN_REMOTE_TOKEN", "gbrain-mcp")
STATE = "ops/people-scan"
BATCH = int(os.environ.get("PEOPLE_SCAN_BATCH", "3"))
DAYS = 90
MAX_ATTENDEES = 10
MAX_EXAMINED = 60
BACKLOG_EVERY_MIN = 5
OUTPUT_DIR = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "cron", "output")
MARKER = "PEOPLE-SCAN-RESULT"
DRY_RUN = os.environ.get("PEOPLE_SCAN_DRY_RUN") == "1"
SKIP_LABELS = {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
AUTOMATED = re.compile(r"(no-?reply|do-?not-?reply|notification|newsletter|mailer|bounce|updates?@|news@|info@|"
                       r"marketing|hello@|support@|team@|^em@|receipt|invoice|billing|statement|customerservice|^service@|"
                       r"^contact@|^office@|^welcome@|^register@|^notify@|^studio@|^help@|calendar-server|@resource\.calendar\.google\.com)", re.I)
SELF = re.compile(r"^(dylan[.a-z]*@(simplesalt\.company|simple-salt\.com|saltyonsecurity\.net|gmail\.com)"
                  r"|admin@simplesalt\.company)$", re.I)


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


def people(addresses):
    out = []
    for a in addresses:
        email = address(a)
        if email and "@" in email and not SELF.match(email) and not AUTOMATED.search(email) and email not in out:
            out.append(email)
    return out


def slug_date(page):
    m = re.search(r"\d{4}-\d{2}-\d{2}", page["slug"])
    return m.group(0) if m else ""


def candidate(page, now, since):
    """("take", item), ("skip", None) for a page that will never qualify, or ("later", None) for a future meeting."""
    fm = (call("get_page", {"slug": page["slug"]}) or {}).get("frontmatter") or {}
    if page["type"] == "meeting":
        start = fm.get("start") or ""
        try:
            when = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            return "skip", None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        attendees = fm.get("attendees") or []
        who = people(attendees)
        if when > now:
            return "later", None
        if when < since or not who or len(attendees) > MAX_ATTENDEES:
            return "skip", None
        return "take", {"slug": page["slug"], "kind": "meeting", "date": start[:10], "title": page.get("title"),
                        "people": who}
    labels = set(fm.get("labels") or [])
    if labels & SKIP_LABELS:
        return "skip", None
    date = (fm.get("date") or "")[:10]
    if not date or date < since.date().isoformat():
        return "skip", None
    who = people(fm.get("participants") or [])
    sender = address(fm.get("from") or "")
    if not who or (AUTOMATED.search(sender) and "SENT" not in labels):
        return "skip", None
    return "take", {"slug": page["slug"], "kind": "email", "date": date, "title": page.get("title"),
            "from": fm.get("from"), "people": who, "dylan_wrote": "SENT" in labels}


def load_state():
    try:
        page = call("get_page", {"slug": STATE})
    except RuntimeError:
        return set()
    body = (page.get("compiled_truth") or "") if isinstance(page, dict) else ""
    m = re.search(r"```json\n(.*?)\n```", body, re.S)
    return set(json.loads(m.group(1))) if m else set()


def applied_marks(done):
    return {s[len("output:"):] for s in done if s.startswith("output:")}


def save_state(done):
    content = ("---\ntype: note\ntitle: People scan state\n---\n\n# People scan state\n\n"
               "Meetings and email threads the people-scan job has handled. Kept by cron-people-scan.py.\n\n"
               "```json\n" + json.dumps(sorted(done)) + "\n```\n")
    call("put_page", {"slug": STATE, "content": content})


def page_slug(name):
    return "people/" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown")


def yaml_str(s):
    return json.dumps(s, ensure_ascii=False)


def write_person(p):
    """Create the person's page, or add this meeting or thread to the page they already have."""
    name, email, item = (p.get("name") or "").strip(), (p.get("email") or "").strip().lower(), p.get("item") or ""
    if not name or "@" not in email or not item:
        return
    entry = {"date": p.get("date") or "", "summary": (p.get("summary") or "Met or emailed Dylan")[:200], "source": item}
    slug = p.get("existing_slug")
    if slug:
        try:
            call("get_page", {"slug": slug})
        except RuntimeError:
            slug = None
    if not slug:
        slug = page_slug(name)
        try:
            call("get_page", {"slug": slug})
            exists = True
        except RuntimeError:
            exists = False
        if not exists:
            fm = ["---", "type: person", f"title: {yaml_str(name)}"]
            if p.get("company"):
                fm.append(f"company: {yaml_str(p['company'])}")
            fm += ["emails:", f"  - {yaml_str(email)}", "aliases:", f"  - {yaml_str(email)}", "---"]
            about = (p.get("about") or "").strip()
            body = f"\n\n# {name}\n\n## About\n\n{about}\n\nSource: {p.get('title') or item}, {entry['date']}.\n"
            call("put_page", {"slug": slug, "content": "\n".join(fm) + body})
            if p.get("dylan_met_or_wrote"):
                call("add_tag", {"slug": slug, "tag": "needs-linkedin"})
    call("add_timeline_entry", dict(entry, slug=slug))


def apply_results(state_files):
    """Write pages for every saved people-scan answer not applied yet; returns the files applied."""
    applied = []
    try:
        jobs = os.listdir(OUTPUT_DIR)
    except OSError:
        return applied
    for job in jobs:
        d = os.path.join(OUTPUT_DIR, job)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            key = f"{job}/{f}"
            if key in state_files or not f.endswith(".md"):
                continue
            text = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
            if MARKER not in text:
                continue
            m = re.search(r"\[.*\]", text.split(MARKER, 1)[1], re.S)
            try:
                people = json.loads(m.group(0)) if m else []
            except json.JSONDecodeError:
                people = []
            for p in people if isinstance(people, list) else []:
                try:
                    write_person(p)
                except Exception as e:
                    print(f"people-scan: could not write {p.get('name')}: {e}", file=sys.stderr)
            applied.append(key)
    return applied


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(days=DAYS)
    done = load_state()
    applied = apply_results(applied_marks(done))
    done |= {"output:" + k for k in applied}
    pages = [p for p in all_pages(type="meeting", source_id="gcal") + all_pages(type="email", source_id="gmail")
             if p["slug"] not in done]
    today = now.date().isoformat()
    pages = [p for p in pages if slug_date(p) <= today]
    pages.sort(key=slug_date, reverse=True)
    fresh = (now - datetime.timedelta(days=2)).date().isoformat()
    if not any(slug_date(p) >= fresh for p in pages) and now.minute % BACKLOG_EVERY_MIN:
        # Only older backlog left: work it every few minutes, so it does not use up the chat model's rate limit.
        if applied and not DRY_RUN:
            save_state(done)
        print(json.dumps({"wakeAgent": False}))
        return
    batch, skipped, examined = [], 0, 0
    for page in pages:
        if len(batch) >= BATCH or examined >= MAX_EXAMINED:
            break
        examined += 1
        verdict, item = candidate(page, now, since)
        if verdict == "take":
            batch.append(item)
            done.add(page["slug"])
        elif verdict == "skip":
            done.add(page["slug"])
            skipped += 1
    if (batch or skipped or applied) and not DRY_RUN:
        save_state(done)
    if not batch:
        print(json.dumps({"wakeAgent": False}))
        return
    print(f"{len(batch)} items to process; {skipped} skipped as automated or out of range; "
          f"{len(pages) - examined} not yet examined.")
    print(json.dumps(batch, indent=1))


if __name__ == "__main__":
    main()
