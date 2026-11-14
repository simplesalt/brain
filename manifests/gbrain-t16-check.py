import json
import sys
import urllib.request

URL = "http://gbrain:3141/mcp"
SLUG = "notes/zz-guard-check-926-1794675256444"


def rpc(name, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": "Bearer gbrain-mcp"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.headers.get("content-type"), r.read().decode()


if sys.argv[1] == "mark":
    ctype, raw = rpc("list_pages", {"limit": 1})
    print(f"before mark: list_pages through the guard -> content-type {ctype}")
    ctype, raw = rpc("mark_page_for_deletion", {"slug": SLUG, "reason": "projects#926 T16 check: open mark while cron scripts run"})
    print(f"mark_page_for_deletion {SLUG} -> {raw[:300]}")
    ctype, raw = rpc("list_pages", {"limit": 1})
    print(f"with the mark open: list_pages through the guard -> content-type {ctype}")
else:
    ctype, raw = rpc("withdraw_page_mark", {"slug": SLUG})
    print(f"withdraw_page_mark {SLUG} -> {raw[:300]}")
