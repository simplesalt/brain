#!/usr/bin/env python3
"""Clear the needs-linkedin tag from gbrain people whose page now has a LinkedIn link.

Runs as a no-agent cron job. The people-scan job tags new people Dylan met or wrote to needs-linkedin.
Dux Soup cannot search LinkedIn by name, so this job no longer searches: the profile URL is found
elsewhere and visited, and duxsoup-ingest fills in the page. A page that has a LinkedIn link drops the
tag. Prints nothing unless it did something.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

scan = import_module("cron-people-scan")

NEEDS = "needs-linkedin"


def main():
    done = []
    for page in scan.all_pages(tag=NEEDS):
        slug = page["slug"]
        fm = (scan.call("get_page", {"slug": slug}) or {}).get("frontmatter") or {}
        if fm.get("linkedin"):
            scan.call("remove_tag", {"slug": slug, "tag": NEEDS})
            done.append(f"{page.get('title')}: LinkedIn profile found")
    if done:
        print("\n".join(done))


if __name__ == "__main__":
    main()
