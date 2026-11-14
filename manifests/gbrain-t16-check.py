import json
import urllib.request

URL = "http://gbrain:3131/mcp"
SLUGS = ["notes/zz-guard-check-926-1794675197506", "notes/zz-guard-check-926-1794675256444"]


def call(name, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": "Bearer gbrain-mcp"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode()
    for line in (["data: " + raw.strip()] if raw.lstrip().startswith("{") else raw.splitlines()):
        if line.startswith("data: "):
            msg = json.loads(line[6:])
            text = "\n".join(c.get("text", "") for c in msg.get("result", {}).get("content", []))
            return bool(msg.get("error") or msg.get("result", {}).get("isError")), text
    return True, raw[:300]


fails = 0
for slug in SLUGS:
    if not slug.startswith("notes/zz-guard-check-926-"):
        raise SystemExit("refusing a slug outside the test prefix")
    err, text = call("get_page", {"slug": slug})
    page = json.loads(text) if not err else {}
    if page.get("slug") != slug:
        print(f"{slug}: not live ({text[:200]})")
        continue
    err, text = call("delete_page", {"slug": slug, "expected_revision": page.get("revision")})
    print(f"delete_page {slug} (revision {page.get('revision')}, {len(page.get('compiled_truth') or '')} bytes) -> error={err} {text[:200]}")
    err, text = call("get_page", {"slug": slug})
    gone = err or json.loads(text).get("slug") != slug
    print(f"after: get_page {slug} -> {'gone' if gone else 'STILL LIVE'} ({text[:160]})")
    fails += 0 if gone else 1
print(f"RESULT: {'PASS' if fails == 0 else 'FAIL'}")
