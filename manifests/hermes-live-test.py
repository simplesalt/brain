import json, os, re, time, urllib.request

HERMES = 'http://hermes-agent-web:8642'
KEY = os.environ['API_SERVER_KEY']
GBRAIN = 'http://gbrain:3131/mcp'
TOKEN = os.environ['GBRAIN_REMOTE_TOKEN']
TAG = os.environ['RUN_TAG']
DAY = time.strftime('%Y-%m-%d', time.gmtime())
PERSON = f'Pat {TAG.title()}'
MEETING = f'meetings/{DAY}-livetest-{TAG}-pentest-scoping'
SIDECAR = f'sources/meetings/{DAY}-livetest-{TAG}-pentest-scoping-transcript'
NOTE = f'notes/{DAY}-livetest-{TAG}-scoping-prep'


def http(url, body=None, token=KEY, timeout=60):
    req = urllib.request.Request(url, method='POST' if body is not None else 'GET')
    req.add_header('Authorization', 'Bearer ' + token)
    if body is not None:
        req.add_header('Content-Type', 'application/json')
        req.add_header('Accept', 'application/json, text/event-stream')
        req.data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors='replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors='replace')
    except Exception as e:
        return 0, repr(e)


def gbrain(name, args):
    s, text = http(GBRAIN, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}, token=TOKEN)
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
            return False, f'HTTP {s}: {text[:300]}'
    if msg.get('error'):
        return False, msg['error']
    out = '\n'.join(c.get('text', '') for c in (msg.get('result') or {}).get('content', []))
    try:
        out = json.loads(out)
    except Exception:
        pass
    return not (msg.get('result') or {}).get('isError'), out


def page(slug):
    ok, p = gbrain('get_page', {'slug': slug, 'include_content': True})
    return p if ok and isinstance(p, dict) else None


def show(label, slug):
    p = page(slug)
    print(f'== {label} {slug}: ' + ('missing' if not p else f"{len((p.get('compiled_truth') or '').encode())} bytes, revision {p.get('revision')}"))
    if p:
        print(json.dumps(p.get('frontmatter')))
        print(p.get('compiled_truth'))
    return p


def cleanup():
    slugs = {MEETING, SIDECAR, NOTE}
    ok, res = gbrain('search', {'query': PERSON})
    for r in res if ok and isinstance(res, list) else []:
        if TAG in (r.get('slug') or ''):
            slugs.add(r['slug'])
    ok, res = gbrain('list_pages', {'limit': 100, 'sort': 'updated_desc'})
    for r in res if ok and isinstance(res, list) else []:
        if TAG in (r.get('slug') or ''):
            slugs.add(r['slug'])
    for slug in sorted(slugs):
        if TAG not in slug:
            continue
        p = page(slug)
        if not p:
            print(f'== cleanup {slug}: not present')
            continue
        print(f"== cleanup delete_page {slug} -> {gbrain('delete_page', {'slug': slug, 'expected_revision': p.get('revision')})}")


if os.environ.get('CLEANUP_ONLY') == '1':
    cleanup()
    raise SystemExit(0)

print(f'== run tag {TAG}, test contact {PERSON}')
transcript = '\n'.join(f'**{s}** [{i}:00]: {t}' for i, (s, t) in enumerate([
    (PERSON, 'Thanks for joining. We want a penetration test of our customer portal before the end of the quarter.'),
    ('Dylan', 'We can do that. I will send you the scoping questionnaire by 2026-11-20.'),
    (PERSON, 'Good. We decided to leave the legacy admin panel out of scope.'),
    ('Dylan', 'Agreed, the legacy admin panel is out of scope.'),
]))
print('== put_page sidecar', gbrain('put_page', {'slug': SIDECAR, 'content':
    f"---\ntype: source\ntitle: 'Transcript: Livetest {TAG} pentest scoping'\nfacts_backstop: false\nmeeting: {MEETING}\nfireflies_id: livetest{TAG}\n---\n\n# Transcript: Livetest {TAG} pentest scoping\n\n{transcript}\n"})[0])
print('== put_page meeting', gbrain('put_page', {'slug': MEETING, 'content':
    f"---\ntype: meeting\ntitle: Livetest {TAG} pentest scoping\ndate: '{DAY}'\nfireflies_id: livetest{TAG}\ntranscript: {SIDECAR}\nfacts_backstop: false\n---\n\n# Livetest {TAG} pentest scoping\n\nTranscript: [[{SIDECAR}]]\n\n## Attendees\n\n- {PERSON} (pat@livetest-{TAG}.invalid), Test contact\n- Dylan\n"})[0])
print('== put_page note', gbrain('put_page', {'slug': NOTE, 'content':
    f"---\ntype: note\ntitle: Livetest {TAG} scoping prep\ndate: '{DAY}'\nsource: handwritten\ntags: [handwritten]\n---\n\n# Livetest {TAG} scoping prep\n\nPrep for the call with {PERSON} (test contact).\n\n## Action items\n\n- [ ] Dylan → send the scoping questionnaire (due 2026-11-20)\n\n## Open questions\n\n- [ ] Is the legacy admin panel in scope?\n"})[0])
show('before', MEETING)
show('before', NOTE)

print('== waiting for the live meeting-ingest cron to pick the meeting up (every 5 minutes)')
deadline = time.time() + 30 * 60
done = False
while time.time() < deadline:
    p = page(MEETING)
    body = (p or {}).get('compiled_truth') or ''
    if '## Summary' in body and 'facts_backstop' not in json.dumps((p or {}).get('frontmatter') or {}):
        done = True
        break
    time.sleep(30)
print(f'== meeting ingested by the live cron: {done} after {int(time.time() - (deadline - 30 * 60))}s')
show('after', MEETING)
show('after (sidecar must be unchanged)', SIDECAR)
ok, state = gbrain('get_page', {'slug': 'ops/meeting-ingest'})
m = re.search(r'```json\n(.*?)\n```', (state or {}).get('compiled_truth') or '', re.S) if ok and isinstance(state, dict) else None
print('== ops/meeting-ingest state mentions the test meeting:', bool(m and MEETING in m.group(1)))

for _ in range(60):
    if http(HERMES + '/health')[0] == 200:
        break
    time.sleep(10)
s, b = http(HERMES + '/v1/models')
try:
    model = json.loads(b)['data'][0]['id']
except Exception:
    model = 'hermes-agent'
msg = (f'On my handwritten note {NOTE}: the legacy admin panel is out of scope, and I sent the scoping questionnaire today. '
       f'Update the note. {PERSON} is a made-up test contact: do not message, email or look up anyone.')
t0 = time.time()
s, b = http(HERMES + '/v1/chat/completions', {'model': model, 'messages': [{'role': 'user', 'content': msg}]}, timeout=900)
print(f'== Hermes chat (note-capture) -> {s} in {time.time() - t0:.0f}s')
try:
    print(json.loads(b)['choices'][0]['message']['content'])
except Exception:
    print(b[:3000])
time.sleep(10)
show('after', NOTE)
print('== done; cleanup runs as a separate Job after the logs are saved')
