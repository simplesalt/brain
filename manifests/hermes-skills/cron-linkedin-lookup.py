#!/usr/bin/env python3
"""Ask Dux Soup to find new people on LinkedIn, so their profile lands on their gbrain page.

Runs as a no-agent cron job. The people-scan job tags new people Dylan met or wrote to needs-linkedin.
For each, this queues one LinkedIn search by name and company; Dux Soup visits the matches and the
duxsoup-ingest service fills in the existing page. A page that has a LinkedIn link drops the tag; a
search with no result after a few days drops it too. Prints nothing unless it did something.
"""
import datetime
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

scan = import_module("cron-people-scan")

MSG_URL = os.environ.get("HERMES_MSG_URL", "http://hermes-msg.ssint-main-msg:8080/mcp")
NEEDS, SEARCHED = "needs-linkedin", "linkedin-searched-"
GIVE_UP_DAYS = 4
PER_RUN = 10


def msg_call(tool, args):
    """One tool call on the hermes-msg MCP server (streamable HTTP, fresh session)."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    def post(body, extra=None):
        req = urllib.request.Request(MSG_URL, data=json.dumps(body).encode(), headers=dict(headers, **(extra or {})))
        return urllib.request.urlopen(req, timeout=60)

    with post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "linkedin-lookup", "version": "1"}}}) as r:
        sid = r.headers.get("Mcp-Session-Id")
        r.read()
    extra = {"Mcp-Session-Id": sid} if sid else {}
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, extra).read()
    with post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": args}}, extra) as r:
        for line in r.read().decode().splitlines():
            if line.startswith("data: ") or line.startswith("{"):
                msg = json.loads(line[6:] if line.startswith("data: ") else line)
                if "error" in msg or msg.get("result", {}).get("isError"):
                    raise RuntimeError(f"{tool}: {msg}")
                return msg["result"]
    raise RuntimeError(f"{tool}: no response")


def main():
    today = datetime.date.today()
    done = []
    for page in scan.all_pages(tag=NEEDS)[:PER_RUN * 3]:
        slug = page["slug"]
        fm = (scan.call("get_page", {"slug": slug}) or {}).get("frontmatter") or {}
        tags = scan.call("get_tags", {"slug": slug}) or []
        searched = sorted(t for t in tags if isinstance(t, str) and t.startswith(SEARCHED))
        if fm.get("linkedin"):
            scan.call("remove_tag", {"slug": slug, "tag": NEEDS})
            done.append(f"{page.get('title')}: LinkedIn profile found")
        elif searched:
            asked = datetime.date.fromisoformat(searched[-1][len(SEARCHED):])
            if (today - asked).days >= GIVE_UP_DAYS:
                scan.call("remove_tag", {"slug": slug, "tag": NEEDS})
                done.append(f"{page.get('title')}: no LinkedIn match after {GIVE_UP_DAYS} days")
        elif len(done) < PER_RUN:
            args = {"name": page.get("title"), "max_results": 3}
            if fm.get("company"):
                args["company"] = fm["company"]
            msg_call("linkedin_search", args)
            scan.call("add_tag", {"slug": slug, "tag": SEARCHED + today.isoformat()})
            done.append(f"{page.get('title')}: LinkedIn search queued")
    if done:
        print("\n".join(done))


if __name__ == "__main__":
    main()
