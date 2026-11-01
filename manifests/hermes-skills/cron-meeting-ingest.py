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
import json
import os
import re
import sys
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
