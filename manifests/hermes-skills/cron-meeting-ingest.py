#!/usr/bin/env python3
"""Pick the next Fireflies meeting that gbrain has saved but Hermes has not ingested yet.

Runs every few minutes as the trigger for the meeting-ingest cron job: it wakes Hermes only when a Fireflies
meeting page under meetings/ has not been handled. It lists the type: meeting pages in gbrain's default source
that carry a fireflies_id (the Fireflies import writes it; other meeting pages are skipped), drops the ones
already handled, and prints ONE meeting for Hermes to run gbrain's meeting-ingestion skill on (calls are long). State
is kept on the gbrain page ops/meeting-ingest: a meeting handed out is pending until a saved job output carries
"MEETING-INGEST-DONE <slug>", then done. A pending meeting with no such line after an hour (the pass failed or was
cut short) is handed out again, at most twice in all, then dropped with a warning.
On the first run only calls from the last 30 days are taken, so an old backlog does not flood the model; older
calls are marked handled without being handed out. With nothing to do it prints {"wakeAgent": false} so the job
skips the model call. MEETING_INGEST_DRY_RUN=1 prints the pick without marking anything.
"""
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

URL = os.environ.get("GBRAIN_URL", "http://gbrain:3131/mcp")
TOKEN = os.environ.get("GBRAIN_REMOTE_TOKEN", "gbrain-mcp")
STATE = "ops/meeting-ingest"
PREFIX = "meetings/"
DAYS = 30
OUTPUT_DIR = os.path.join(os.environ.get("HERMES_HOME", "/opt/data"), "cron", "output")
DONE_MARKER = "MEETING-INGEST-DONE"
RETRY_AFTER = datetime.timedelta(hours=1)
MAX_TRIES = 2
ADDON_URL = os.environ.get("ADDON_URL", "http://gbrain.ssint-main-ai:3140").rstrip("/")
ADDON_TOKEN = os.environ.get("ADDON_TOKEN", "gbrain-addon")
DYLAN_NAMES = {"dylan", "dylan evans", "dylan e", "dylan e.", "me", "i"}
DYLAN_EMAILS = {"dylan@simple-salt.com", "dylan.evans@simplesalt.company"}
DRY_RUN = os.environ.get("MEETING_INGEST_DRY_RUN") == "1"


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


def slug_date(page):
    m = re.search(r"\d{4}-\d{2}-\d{2}", page["slug"])
    return m.group(0) if m else ""


def is_fireflies(page):
    """True when the page's frontmatter has a fireflies_id. list_pages rows may not carry frontmatter, so
    read the page when the row does not say."""
    fm = page.get("frontmatter")
    if isinstance(fm, dict):
        return bool(fm.get("fireflies_id"))
    try:
        full = call("get_page", {"slug": page["slug"], "include_content": True})
    except RuntimeError:
        return False
    if not isinstance(full, dict):
        return False
    fm = full.get("frontmatter")
    if isinstance(fm, dict):
        return bool(fm.get("fireflies_id"))
    head = re.match(r"---\n(.*?)\n---", full.get("content") or "", re.S)
    return bool(head and re.search(r"^fireflies_id:\s*\S", head.group(1), re.M))


def load_state():
    """Returns (done slugs, pending {slug: {"at", "tries"}}, state page revision or None, whether it exists).
    An older state page holds a plain list of done slugs."""
    try:
        page = call("get_page", {"slug": STATE})
    except RuntimeError:
        return set(), {}, None, False
    if not isinstance(page, dict):
        return set(), {}, None, False
    m = re.search(r"```json\n(.*?)\n```", page.get("compiled_truth") or "", re.S)
    data = json.loads(m.group(1)) if m else []
    if isinstance(data, list):
        return set(data), {}, page.get("revision"), True
    return set(data.get("done", [])), dict(data.get("pending", {})), page.get("revision"), True


def save_state(done, pending, revision):
    state = {"done": sorted(done), "pending": dict(sorted(pending.items()))}
    content = ("---\ntype: note\ntitle: Meeting ingest state\n---\n\n# Meeting ingest state\n\n"
               "Fireflies calls the meeting-ingest job has handed to Hermes: done, or pending until Hermes reports "
               "them done. Kept by cron-meeting-ingest.py.\n\n"
               "```json\n" + json.dumps(state, indent=1) + "\n```\n")
    args = {"slug": STATE, "content": content}
    if revision is not None:
        args["expected_revision"] = revision
    call("put_page", args)


def finished_slugs():
    """Slugs named on a DONE_MARKER line in any saved job output."""
    found = set()
    try:
        jobs = os.listdir(OUTPUT_DIR)
    except OSError:
        return found
    for job in jobs:
        d = os.path.join(OUTPUT_DIR, job)
        for f in os.listdir(d) if os.path.isdir(d) else []:
            if not f.endswith(".md"):
                continue
            text = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
            found |= set(re.findall(DONE_MARKER + r"\s+(" + re.escape(PREFIX) + r"[^\s:]+)", text))
    return found


def addon_post(path, body):
    req = urllib.request.Request(ADDON_URL + path, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + ADDON_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}


def action_items(body):
    """Parse the '## Action Items' section into [{owner, text, owed_to, due}]. Line format (see the prompt):
    '- <Owner>: <task> [owed to: <Person>] [due: YYYY-MM-DD]'; owner 'unknown' when not stated."""
    m = re.search(r"^##\s+Action Items\s*\n(.*?)(?=^##\s|\Z)", body or "", re.S | re.M)
    items = []
    for line in (m.group(1) if m else "").splitlines():
        t = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*(?:\[[ xX]\]\s*)?", "", line).strip()
        if not t or t == line.strip() and not line.lstrip().startswith(("-", "*", "+")) and not re.match(r"\s*\d+[.)]", line):
            continue
        if t.startswith("_") or re.match(r"(?i)(none|no action items)", t):
            continue
        om = re.match(r"\**([^:*]{1,60}?)\**\s*:\s*(.+)$", t)
        owner, text = (om.group(1).strip(), om.group(2).strip()) if om else ("unknown", t)
        due = re.search(r"(?i)\bdue\b[^0-9]{0,12}(\d{4}-\d{2}-\d{2})", text)
        owed = re.search(r"(?i)\[?\bowed to:?\s*([^\]\);]+)", text)
        items.append({"owner": owner, "text": text, "due": due.group(1) if due else None,
                      "owed_to": owed.group(1).strip() if owed else None})
    return items


def is_dylan(name):
    n = (name or "").strip().lower()
    return n in DYLAN_NAMES or n in DYLAN_EMAILS or n.startswith("dylan ")


def person_slug(name):
    """gbrain person page slug for a name, or None. Pages marked Test contact are returned as 'TEST'."""
    try:
        res = call("search", {"query": name, "types": ["person"], "limit": 5})
    except RuntimeError:
        return None
    rows = res.get("results") if isinstance(res, dict) else res
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and str(r.get("slug", "")).startswith("people/") \
                and (r.get("title") or "").strip().lower() == name.strip().lower():
            return r["slug"]
    return None


def is_test_contact(slug):
    try:
        page = call("get_page", {"slug": slug, "include_content": True})
    except RuntimeError:
        return False
    return isinstance(page, dict) and "test contact" in json.dumps(page).lower()


def open_loops(slug):
    """Turn the Action Items of a saved meeting page into open loops. Returns the summary string, or None
    when the addon was unreachable (the caller retries on a later run). Idempotent on the addon side."""
    page = call("get_page", {"slug": slug, "include_content": True})
    if not isinstance(page, dict):
        return "loops: no page"
    body = page.get("compiled_truth") or page.get("content") or ""
    # Test contacts get no loops.
    att = re.search(r"^##\s+Attendees\s*\n(.*?)(?=^##\s|\Z)", body, re.S | re.M)
    if re.search(r"(?i)test contact", att.group(1) if att else ""):
        return "loops: skipped (test contact)"
    attendees = re.findall(r"^-\s+(.+?)\s*<([^>]+)>\s*$", att.group(1) if att else "", re.M)
    external = [(n, e) for n, e in attendees if e.lower() not in DYLAN_EMAILS]
    main_ext = external[0] if external else None
    opened = skipped = failed = 0
    for it in action_items(body):
        text = it["text"]
        eid = slug + "#" + hashlib.sha1(re.sub(r"\s+", " ", text.lower()).encode()).hexdigest()[:12]
        req = {"channel": "fireflies", "external_id": eid, "thread_id": slug, "summary": text[:500],
               "evidence": [{"page_slug": slug, "quote": text[:500]}]}
        if it["due"]:
            req["due_at"] = it["due"]
        owner = it["owner"]
        if re.match(r"(?i)(unknown|unassigned|tbd|n/a|someone|team|everyone)\b", owner):
            skipped += 1
            continue
        if is_dylan(owner):
            req["loop_type"] = "commitment_owed_by_me"
            who = it["owed_to"]
            if who:
                s = person_slug(who)
                if s:
                    req["counterparty_slug"] = s
            elif main_ext:
                s = person_slug(main_ext[0])
                if s:
                    req["counterparty_slug"] = s
                req["counterparty_email"] = main_ext[1]
        else:
            s = person_slug(owner)
            if s and is_test_contact(s):
                skipped += 1
                continue
            req["loop_type"] = "commitment_owed_to_me"
            if s:
                req["counterparty_slug"] = s
            else:
                em = [e for n, e in attendees if n.strip().lower() == owner.strip().lower()]
                if em:
                    req["counterparty_email"] = em[0]
        try:
            code, resp = addon_post("/loops/open", req)
        except OSError as e:
            print(f"meeting-ingest: loops addon unreachable for {slug}: {e}", file=sys.stderr)
            return None
        if code == 200:
            opened += 1
        elif code == 400:
            failed += 1
            print(f"meeting-ingest: loop rejected for {slug}: {resp}", file=sys.stderr)
        else:
            print(f"meeting-ingest: loops addon HTTP {code} for {slug}", file=sys.stderr)
            return None
    return f"loops: {opened} opened, {skipped} skipped (unknown owner), {failed} rejected"


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(days=DAYS)).date().isoformat()
    today = now.date().isoformat()
    done, pending, revision, exists = load_state()
    before = (set(done), json.dumps(pending, sort_keys=True))
    finished = finished_slugs()
    retry = None
    for slug, p in sorted(pending.items(), key=lambda kv: kv[1]["at"]):
        if slug in finished:
            # Hermes has saved the page: open loops for its action items now (deterministic, idempotent). If
            # the addon is unreachable, stay pending and try again on the next run.
            if DRY_RUN:
                continue
            try:
                result = open_loops(slug)
            except RuntimeError as e:
                print(f"meeting-ingest: reading {slug} for loops failed: {e}", file=sys.stderr)
                result = None
            if result is None:
                continue
            print(f"meeting-ingest: {slug} {result}", file=sys.stderr)
            done.add(slug)
        elif now - datetime.datetime.fromisoformat(p["at"]) < RETRY_AFTER:
            continue
        elif p["tries"] >= MAX_TRIES:
            print(f"meeting-ingest: giving up on {slug} after {p['tries']} passes with no {DONE_MARKER}",
                  file=sys.stderr)
            done.add(slug)
        elif retry is None:
            retry = slug
    for slug in done:
        pending.pop(slug, None)
    pages = [p for p in all_pages(type="meeting", source_id="default")
             if p["slug"].startswith(PREFIX) and p["slug"] not in done and p["slug"] not in pending]
    fresh = [p for p in pages if since <= slug_date(p) <= today]
    # Calls older than the window (or with no date in the slug) are never handed out; mark them so they
    # are not listed again.
    stale = {p["slug"] for p in pages if not (since <= slug_date(p) <= today)}
    fresh.sort(key=slug_date, reverse=True)
    # Pick the newest fresh meeting that came from Fireflies. Non-Fireflies meetings are marked handled so
    # they are not read again.
    # A retry of a failed pass goes first.
    pick, other = None, set()
    if retry:
        pick = {"slug": retry, "title": None}
    else:
        for p in fresh:
            if is_fireflies(p):
                pick = p
                break
            other.add(p["slug"])
    stale |= other
    fresh = [p for p in fresh if p["slug"] not in other and p is not pick]
    done |= stale
    if pick:
        tries = pending.get(pick["slug"], {}).get("tries", 0) + 1
        pending[pick["slug"]] = {"at": now.isoformat(), "tries": tries}
    if not DRY_RUN and ((set(done), json.dumps(pending, sort_keys=True)) != before or not exists):
        save_state(done, pending, revision)
    if not pick:
        print(json.dumps({"wakeAgent": False}))
        return
    again = " (again: the last pass reported no result)" if retry else ""
    print(f"1 meeting to ingest{again}; {len(fresh)} more waiting.")
    print(json.dumps({"slug": pick["slug"], "title": pick.get("title"), "date": slug_date(pick)}, indent=1))


if __name__ == "__main__":
    main()
