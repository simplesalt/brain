#!/usr/bin/env python3
"""Pick the next meetings and email threads whose people gbrain has not met yet.

Runs before the people-scan cron job. It lists Dylan's synced meetings (past ones only) and email
threads from gbrain, drops the ones already tagged people-scanned, newsletters and automated mail,
and prints a batch for Hermes to turn into people pages. With nothing to do it prints
{"wakeAgent": false} so the job skips the model call.
"""
import datetime
import json
import os
import re
import urllib.request

URL = os.environ.get("GBRAIN_URL", "http://gbrain:3131/mcp")
TOKEN = os.environ.get("GBRAIN_REMOTE_TOKEN", "gbrain-mcp")
TAG = "people-scanned"
BATCH = int(os.environ.get("PEOPLE_SCAN_BATCH", "12"))
DAYS = 90
MAX_ATTENDEES = 10
MAX_EXAMINED = 200
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


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(days=DAYS)
    done = {p["slug"] for p in all_pages(tag=TAG)}
    pages = [p for p in all_pages(type="meeting", source_id="gcal") + all_pages(type="email", source_id="gmail")
             if p["slug"] not in done]
    today = now.date().isoformat()
    pages = [p for p in pages if slug_date(p) <= today]
    pages.sort(key=slug_date, reverse=True)
    batch, skipped, examined = [], 0, 0
    for page in pages:
        if len(batch) >= BATCH or examined >= MAX_EXAMINED:
            break
        examined += 1
        verdict, item = candidate(page, now, since)
        if verdict == "take":
            batch.append(item)
        elif verdict == "skip":
            if not DRY_RUN:
                call("add_tag", {"slug": page["slug"], "tag": TAG})
            skipped += 1
    if not batch:
        print(json.dumps({"wakeAgent": False}))
        return
    print(f"{len(batch)} items to process; {skipped} skipped as automated or out of range; "
          f"{len(pages) - examined} not yet examined.")
    print(json.dumps(batch, indent=1))


if __name__ == "__main__":
    main()
