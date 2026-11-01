#!/usr/bin/env python3
"""Pick the next Fireflies call that gbrain has saved but Hermes has not ingested yet.

Runs every few minutes as the trigger for the meeting-ingest cron job: it wakes Hermes only when a call page
under calls/ has not been handled. It lists the call pages in gbrain's default source, drops the ones already
handled, and prints ONE call for Hermes to run gbrain's meeting-ingestion skill on (calls are long). Handled
slugs are kept on the gbrain page ops/meeting-ingest, marked when handed out so no call is processed twice.
On the first run only calls from the last 30 days are taken, so an old backlog does not flood the model; older
calls are marked handled without being handed out. With nothing to do it prints {"wakeAgent": false} so the job
skips the model call. MEETING_INGEST_DRY_RUN=1 prints the pick without marking anything.
"""
import datetime
import json
import os
import re
import urllib.request

URL = os.environ.get("GBRAIN_URL", "http://gbrain:3131/mcp")
TOKEN = os.environ.get("GBRAIN_REMOTE_TOKEN", "gbrain-mcp")
STATE = "ops/meeting-ingest"
PREFIX = "calls/"
DAYS = 30
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


def load_state():
    """Returns (handled slugs, state page revision or None, whether the state page exists)."""
    try:
        page = call("get_page", {"slug": STATE})
    except RuntimeError:
        return set(), None, False
    if not isinstance(page, dict):
        return set(), None, False
    m = re.search(r"```json\n(.*?)\n```", page.get("compiled_truth") or "", re.S)
    return (set(json.loads(m.group(1))) if m else set()), page.get("revision"), True


def save_state(done, revision):
    content = ("---\ntype: note\ntitle: Meeting ingest state\n---\n\n# Meeting ingest state\n\n"
               "Fireflies calls the meeting-ingest job has handed to Hermes. Kept by cron-meeting-ingest.py.\n\n"
               "```json\n" + json.dumps(sorted(done)) + "\n```\n")
    args = {"slug": STATE, "content": content}
    if revision is not None:
        args["expected_revision"] = revision
    call("put_page", args)


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(days=DAYS)).date().isoformat()
    today = now.date().isoformat()
    done, revision, exists = load_state()
    pages = [p for p in all_pages(type="call", source_id="default")
             if p["slug"].startswith(PREFIX) and p["slug"] not in done]
    fresh = [p for p in pages if since <= slug_date(p) <= today]
    # Calls older than the window (or with no date in the slug) are never handed out; mark them so they
    # are not listed again.
    stale = {p["slug"] for p in pages if not (since <= slug_date(p) <= today)}
    fresh.sort(key=slug_date, reverse=True)
    pick = fresh[0] if fresh else None
    if not DRY_RUN:
        marked = done | stale | ({pick["slug"]} if pick else set())
        if marked != done or not exists:
            save_state(marked, revision)
    if not pick:
        print(json.dumps({"wakeAgent": False}))
        return
    print(f"1 call to ingest; {len(fresh) - 1} more waiting.")
    print(json.dumps({"slug": pick["slug"], "title": pick.get("title"), "date": slug_date(pick)}, indent=1))


if __name__ == "__main__":
    main()
