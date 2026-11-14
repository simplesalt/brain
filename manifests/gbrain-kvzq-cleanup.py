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
    data = [p for p in parts if not isinstance(p, str)]
    if data:
        parts = data
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


PAGES = ['life/events/2026-11-12-9b6c37a9', 'life/events/2026-11-12-cabe0146']
OWNERS = ['people/dylan-evans', 'companies/simple-salt']


def live(slug):
    ok, p = call('get_page', {'slug': slug})
    return p if ok and isinstance(p, dict) and p.get('slug') == slug and not p.get('deleted_at') else None


def timeline(slug):
    ok, res = call('get_timeline', {'slug': slug})
    if isinstance(res, dict):
        res = res.get('entries') or res.get('timeline') or []
    return res if isinstance(res, list) else []


def is_tag(e):
    return TAG in json.dumps(e).lower()


ok, tl = gbrain('tools/list', {})
tools = {t['name']: t for t in (tl.get('tools') or [])} if ok else {}
tltools = sorted(n for n in tools if 'timeline' in n)
print('== timeline tools:', tltools)
for n in tltools:
    print(f'== {n} schema:', json.dumps(tools[n].get('inputSchema'))[:800])

before = recent()
print(f'== list_pages parsed {len(before)} recent pages')
if not before:
    print('RESULT FAIL: list_pages returned nothing parseable')
    raise SystemExit(1)

for s in PAGES:
    p = live(s)
    if not p:
        print(f'== {s}: not live, nothing to do')
        continue
    print(f"== {s}: title {p.get('title')!r}, type {p.get('type')}, body mentions kvzq: {TAG in json.dumps(p).lower()}")
    print(f"== delete_page {s} (revision {p.get('revision')}) -> {call('delete_page', {'slug': s, 'expected_revision': p.get('revision')})}")

remover = next((n for n in tltools if any(w in n for w in ('remove', 'delete'))), None)
for s in OWNERS:
    p = live(s)
    entries = timeline(s)
    tagged = [e for e in entries if isinstance(e, dict) and is_tag(e)]
    print(f"== {s}: revision {(p or {}).get('revision')}, {len(entries)} timeline entries, {len(tagged)} mention kvzq")
    for e in tagged:
        print(f'   kvzq entry: {json.dumps(e)[:400]}')
    tltext = (p or {}).get('timeline') or ''
    print(f"   page timeline text lines with kvzq: {[l.strip()[:200] for l in tltext.splitlines() if TAG in l.lower()]}")
    if not remover:
        print(f'   no timeline removal tool; entries left in place')
        continue
    props = set(((tools[remover].get('inputSchema') or {}).get('properties')) or {})
    for e in tagged:
        args = {k: e[k] for k in ('id', 'entry_id') if k in e and k in props}
        if 'id' in props and 'id' not in args and 'id' in e:
            args['id'] = e['id']
        if 'slug' in props:
            args['slug'] = s
        if not any(k in args for k in ('id', 'entry_id')):
            print(f'   cannot address entry {json.dumps(e)[:200]} with {sorted(props)}; left in place')
            continue
        print(f'   {remover} {args} -> {call(remover, args)}')
    after_entries = timeline(s)
    print(f"   after: {len(after_entries)} entries, {len([e for e in after_entries if isinstance(e, dict) and is_tag(e)])} mention kvzq")

after = recent()
changed = sorted(s for s in after if s not in PAGES and s not in OWNERS and after[s] != before.get(s))
print(f'== other pages changed while this ran: {changed}')
for s in changed:
    print(f"   {s}: before {json.dumps(before.get(s))[:300]} after {json.dumps(after[s])[:300]}")
left_pages = [s for s in PAGES if live(s)]
left_entries = {s: len([e for e in timeline(s) if isinstance(e, dict) and is_tag(e)]) for s in OWNERS}
print(f'== kvzq pages still live: {left_pages}; kvzq timeline entries left: {left_entries}')
ok_all = not left_pages and not any(left_entries.values())
print('RESULT', 'PASS' if ok_all else 'PARTIAL')
