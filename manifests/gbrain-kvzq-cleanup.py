import json, os, urllib.request

GBRAIN = 'http://gbrain:3131/mcp'
TOKEN = os.environ['GBRAIN_REMOTE_TOKEN']
TAG = 'kvzq'


def gbrain(method, params):
    req = urllib.request.Request(GBRAIN, method='POST')
    req.add_header('Authorization', 'Bearer ' + TOKEN)
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/json, text/event-stream')
    req.data = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read().decode(errors='replace')
    except urllib.error.HTTPError as e:
        return False, f'HTTP {e.code}: {e.read().decode(errors="replace")[:300]}'
    msg = None
    for line in text.splitlines():
        if line.startswith('data:'):
            try:
                msg = json.loads(line[5:].strip())
            except Exception:
                pass
    if msg is None:
        try:
            msg = json.loads(text)
        except Exception:
            return False, text[:300]
    if msg.get('error'):
        return False, msg['error']
    return True, msg.get('result') or {}


def call(name, args):
    ok, res = gbrain('tools/call', {'name': name, 'arguments': args})
    if not ok:
        return False, res
    parts = []
    for c in res.get('content', []):
        try:
            parts.append(json.loads(c.get('text', '')))
        except Exception:
            parts.append(c.get('text', ''))
    if len(parts) == 1:
        out = parts[0]
    elif parts and all(isinstance(p, list) for p in parts):
        out = [x for p in parts for x in p]
    elif parts and all(isinstance(p, dict) for p in parts):
        out = [x for p in parts for x in (rows(p) or [p])]
    else:
        out = parts
    return not res.get('isError'), out


def rows(res):
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for k in ('pages', 'results', 'links', 'backlinks', 'items'):
            if isinstance(res.get(k), list):
                return res[k]
    return []


def recent():
    ok, res = call('list_pages', {'limit': 200, 'sort': 'updated_desc'})
    return {r.get('slug'): r for r in rows(res) if isinstance(r, dict) and r.get('slug')}


KNOWN = [
    'meetings/2026-11-12-livetest-kvzq-pentest-scoping',
    'sources/meetings/2026-11-12-livetest-kvzq-pentest-scoping-transcript',
    'notes/2026-11-12-livetest-kvzq-scoping-prep',
    'people/pat-kvzq',
    'companies/livetest-kvzq',
]


def live(slug):
    ok, p = call('get_page', {'slug': slug})
    return p if ok and isinstance(p, dict) and p.get('slug') == slug and not p.get('deleted_at') else None


def find_tag_slugs():
    slugs = set(s for s in recent() if TAG in s) | set(s for s in KNOWN if live(s))
    for q in (TAG, 'Pat Kvzq', 'Livetest Kvzq'):
        ok, res = call('search', {'query': q, 'limit': 50})
        for r in rows(res):
            s = r.get('slug') if isinstance(r, dict) else None
            if s and TAG in s:
                slugs.add(s)
    return slugs


def link_key(l, own, direction):
    if direction == 'out':
        other = l.get('to_slug') or l.get('to') or l.get('slug')
    else:
        other = l.get('from_slug') or l.get('from') or l.get('slug')
    frm, to = (own, other) if direction == 'out' else (other, own)
    return frm, to, l.get('link_type') or l.get('type') or ''


ok, tl = gbrain('tools/list', {})
tools = {t['name']: t for t in (tl.get('tools') or [])} if ok else {}
print('== link tools:', sorted(n for n in tools if 'link' in n))
rm = tools.get('remove_link')
if rm:
    print('== remove_link schema:', json.dumps(rm.get('inputSchema'))[:800])

before = recent()
print(f'== list_pages parsed {len(before)} recent pages')
if not before:
    print('RESULT FAIL: list_pages returned nothing parseable')
    raise SystemExit(1)
targets = sorted(find_tag_slugs())
print(f'== kvzq slugs found ({len(targets)}):')
for s in targets:
    print('  ', s)

ok, st = call('get_page', {'slug': 'ops/meeting-ingest', 'include_content': True})
body = (st or {}).get('compiled_truth') if isinstance(st, dict) else ''
print('== ops/meeting-ingest lines mentioning kvzq:', [l.strip() for l in (body or '').splitlines() if TAG in l][:10])

links = set()
for s in targets:
    for tool, direction in (('get_links', 'out'), ('get_backlinks', 'in')):
        ok, res = call(tool, {'slug': s})
        for l in rows(res):
            if isinstance(l, dict):
                k = link_key(l, s, direction)
                print(f'== {tool} {s}: {json.dumps(l)[:300]}')
                if k[0] and k[1]:
                    links.add(k)

props = set(((rm or {}).get('inputSchema') or {}).get('properties') or {})
for frm, to, typ in sorted(links):
    if not rm:
        print(f'== link {frm} -> {to} ({typ}): no remove_link tool, left in place')
        continue
    args = {('from' if 'from' in props else 'from_slug'): frm, ('to' if 'to' in props else 'to_slug'): to}
    if typ:
        args['link_type' if 'link_type' in props or 'type' not in props else 'type'] = typ
    print(f'== remove_link {frm} -> {to} ({typ}) -> {call("remove_link", args)}')

for s in targets:
    ok, p = call('get_page', {'slug': s})
    if not ok or not isinstance(p, dict):
        print(f'== delete_page {s}: not present ({p})')
        continue
    print(f"== delete_page {s} (revision {p.get('revision')}) -> {call('delete_page', {'slug': s, 'expected_revision': p.get('revision')})}")

after = recent()
changed = sorted(s for s in after if TAG not in s and after[s] != before.get(s))
print(f'== non-kvzq pages changed while this ran: {changed}')
for s in changed:
    print(f"   {s}: before {json.dumps(before.get(s))[:300]} after {json.dumps(after[s])[:300]}")

left = sorted(find_tag_slugs())
print(f'== kvzq slugs still visible by default: {left}')
ok, res = call('search', {'query': TAG, 'limit': 50})
mentions = sorted({r.get('slug') for r in rows(res) if isinstance(r, dict) and r.get('slug') and TAG not in r['slug']})
print(f'== other pages whose text matches kvzq (left unchanged): {mentions}')
print('RESULT', 'PASS' if not left else 'FAIL')
raise SystemExit(0 if not left else 1)
