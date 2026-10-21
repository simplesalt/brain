#!/usr/bin/env python3
"""List today's meetings, in Dylan's time zone, for the daily-brief cron job."""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

scan = import_module("cron-people-scan")

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get("DYLAN_TZ", "America/Chicago"))
except Exception:
    TZ = datetime.timezone(datetime.timedelta(hours=-5))


def main():
    today = datetime.datetime.now(TZ).date()
    near = {(today + datetime.timedelta(days=d)).isoformat() for d in (-1, 0, 1)}
    meetings = []
    for page in scan.all_pages(type="meeting", source_id="gcal"):
        if scan.slug_date(page) not in near:
            continue
        fm = (scan.call("get_page", {"slug": page["slug"]}) or {}).get("frontmatter") or {}
        try:
            start = datetime.datetime.fromisoformat((fm.get("start") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo is None or start.astimezone(TZ).date() != today:
            continue
        meetings.append({"slug": page["slug"], "title": page.get("title"), "start": fm.get("start"),
                         "end": fm.get("end"), "people": scan.people(fm.get("attendees") or [])})
    meetings.sort(key=lambda m: m["start"] or "")
    print(f"Today is {today:%A, %B %-d, %Y}. Meetings today ({len(meetings)}):")
    print(json.dumps(meetings, indent=1))


if __name__ == "__main__":
    main()
