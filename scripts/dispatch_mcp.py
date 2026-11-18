#!/usr/bin/env python3
import hashlib
import json
import os
import re
import signal
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get('PORT', '8080'))
SCOPES_PATH = os.environ.get('SCOPES_PATH', '/etc/dispatch/scopes.json')
MODEL = os.environ.get('MODEL', '').strip()
OPENROUTER_URL = os.environ.get('OPENROUTER_URL', 'https://openrouter.ai/api/v1/chat/completions')
KEY_PATH = os.environ.get('KEY_PATH', '/secrets/openrouter/OPENROUTER_API_KEY')
MODEL_TIMEOUT = float(os.environ.get('MODEL_TIMEOUT_S', '15'))
DISPATCH_TIMEOUT = float(os.environ.get('DISPATCH_TIMEOUT_S', '45'))
MAX_BODY = 1 << 20
MAX_TEXT = 20_000
SLUG = re.compile(r'^[a-z0-9-]{1,48}$')

with open(SCOPES_PATH, encoding='utf-8') as f:
    REGISTRY = json.load(f)
FALLBACK = REGISTRY['fallback']
BUILDERS = REGISTRY['builders']
QUESTIONS = REGISTRY['questions']
TABLE = REGISTRY['table']
AGENTS = REGISTRY['agents']
if FALLBACK not in BUILDERS:
    raise SystemExit(f"fallback builder '{FALLBACK}' is not in the registry")
for row in TABLE:
    if row['builder'] not in BUILDERS:
        raise SystemExit(f"table names unknown builder '{row['builder']}'")
    for q in ('layer', 'target'):
        if row[q] != '*' and row[q] not in QUESTIONS[q]:
            raise SystemExit(f"table row {row} uses unknown {q}")
if set(AGENTS) != set(BUILDERS):
    raise SystemExit('agents must describe exactly the builders')


def router_prompt():
    lines = [
        'Classify the task with two questions.',
        'Answer with only a JSON object: {"layer": "<layer>", "target": "<target>"}.',
        'If you cannot tell, answer "unsure" for that value.',
    ]
    hints = REGISTRY.get('hints', [])
    if hints:
        lines += ['', 'rules'] + [f'- {h}' for h in hints]
    for q in ('layer', 'target'):
        lines += ['', q]
        lines += [f'- {name}: {desc}' for name, desc in QUESTIONS[q].items()]
    return '\n'.join(lines)


PROMPT = router_prompt()


def lookup(layer, target):
    for row in TABLE:
        if row['layer'] in ('*', layer) and row['target'] in ('*', target):
            return row['builder']
    return None


def openrouter_key():
    try:
        with open(KEY_PATH, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return os.environ.get('OPENROUTER_API_KEY', '').strip()


def parse_answer(content):
    if not isinstance(content, str):
        return None, None
    m = re.search(r'\{.*\}', content, re.S)
    if not m:
        return None, None
    try:
        doc = json.loads(m.group(0))
    except ValueError:
        return None, None
    if not isinstance(doc, dict):
        return None, None

    def known(q):
        words = re.findall(r'[a-z]+', str(doc.get(q, '')).lower())
        return words[0] if words and words[0] in QUESTIONS[q] else None

    return known('layer'), known('target')


def post_json(url, payload, timeout, headers=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
                                 headers={'content-type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    try:
        doc = json.loads(body or b'null')
    except ValueError:
        doc = None
    return status, doc


def err_text(e):
    if isinstance(e, TimeoutError) or 'timed out' in str(e):
        return 'timeout'
    if isinstance(e, urllib.error.URLError):
        return str(e.reason)
    return str(e)


def pick_builder(text):
    route = {'layer': None, 'target': None}
    key = openrouter_key()
    if not MODEL:
        return FALLBACK, {**route, 'route': 'fallback', 'route_reason': 'no model configured'}
    if not key:
        return FALLBACK, {**route, 'route': 'fallback', 'route_reason': 'no OpenRouter key'}
    try:
        status, doc = post_json(OPENROUTER_URL, {
            'model': MODEL,
            'temperature': 0,
            'max_tokens': 400,
            'messages': [{'role': 'system', 'content': PROMPT}, {'role': 'user', 'content': text}],
        }, MODEL_TIMEOUT, {'authorization': f'Bearer {key}'})
    except Exception as e:
        return FALLBACK, {**route, 'route': 'fallback', 'route_reason': f'model error: {err_text(e)}'}
    if status != 200:
        return FALLBACK, {**route, 'route': 'fallback', 'route_reason': f'model answered {status}'}
    try:
        content = doc['choices'][0]['message']['content']
    except (TypeError, KeyError, IndexError):
        content = None
    layer, target = parse_answer(content)
    route = {'layer': layer, 'target': target}
    builder = lookup(layer, target) if target else None
    if not builder:
        answer = content[:200] if isinstance(content, str) else None
        return FALLBACK, {**route, 'route': 'fallback', 'route_reason': 'no table row fits the answer',
                          'model_answer': answer}
    return builder, {**route, 'route': 'table'}


def post_dispatch(url, payload):
    try:
        status, doc = post_json(url, payload, DISPATCH_TIMEOUT)
    except Exception as e:
        reason = err_text(e)
        return (504 if reason == 'timeout' else 502), None, f'agent endpoint unreachable: {reason}'
    doc = doc if isinstance(doc, dict) else {}
    addr = doc.get('address')
    addr = addr if isinstance(addr, str) and addr.startswith('bridge:') else None
    if 200 <= status < 300 and not addr:
        return 502, None, f'agent endpoint answered {status} with no bridge address'
    return status, addr, None if addr else str(doc.get('error') or doc.get('reason') or f'agent endpoint answered {status}')


def dispatch(args):
    args = args if isinstance(args, dict) else {}
    text, slug, caller, hops = args.get('text'), args.get('slug'), args.get('caller'), args.get('hops', 0)
    log = {
        'ts': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
        'event': 'dispatch',
        'caller': caller if isinstance(caller, str) else None,
        'text_sha256': hashlib.sha256((text if isinstance(text, str) else '').encode()).hexdigest(),
    }

    def finish(status, message, address=None, route=None):
        print(json.dumps({**log, **(route or {}), 'status': status, 'address': address, 'log': message}), flush=True)
        return {'status': status, 'log': message, 'address': address}

    if not isinstance(text, str) or not text.strip():
        return finish(400, 'text is required')
    if len(text) > MAX_TEXT:
        return finish(400, f'text is over {MAX_TEXT} characters')
    if not isinstance(slug, str) or not SLUG.match(slug):
        return finish(400, 'slug must match [a-z0-9-]{1,48}')
    if not isinstance(caller, str) or not caller.startswith('bridge:') or re.search(r'\s', caller):
        return finish(400, "caller must be a bridge address starting 'bridge:'")
    if isinstance(hops, bool) or not isinstance(hops, int) or hops < 0:
        return finish(400, 'hops must be a non-negative integer')
    builder, route = pick_builder(text)
    route = {'pod': builder, **{k: v for k, v in route.items() if v is not None}}
    status, addr, reason = post_dispatch(BUILDERS[builder], {'slug': slug, 'caller': caller, 'text': text, 'hops': hops})
    if addr:
        return finish(status, f'assigned to {builder}; the new session will reply to {caller}', addr, route)
    return finish(status, f'{builder} did not take the work: {reason}', None, route)


AGENT_NAMES = {'basis': 'Basis', 'cloud-basis': 'Cloud-Basis', 'brain': 'Brain', 'oci': 'OCI', 'projects': 'Projects'}

TOOL = {
    'name': 'dispatch',
    'description': '\n'.join([
        'Assign work to a new session. Available agents are',
        *(f'- {AGENT_NAMES.get(name, name)}: {desc}' for name, desc in AGENTS.items()),
        'Concisely describe what you need the agent to make true for your need to be met. '
        'The agent knows best how to deliver it within its scope; omit any instructions or detail '
        'unnecessary for completing the task.',
        "Returns the agent endpoint's HTTP status, a log line, and the new session's bridge address.",
    ]),
    'inputSchema': {
        'type': 'object',
        'properties': {
            'text': {'type': 'string', 'description': 'What you need made true. The new session receives it as its prompt.'},
            'slug': {'type': 'string', 'description': 'Short name for the new session, [a-z0-9-]{1,48}; it leads the session title.'},
            'caller': {'type': 'string', 'description': "Your own bridge address (starts 'bridge:'); the new session replies there."},
            'hops': {'type': 'integer', 'minimum': 0, 'description': 'How many dispatches led to you; 0 when a person started you.'},
        },
        'required': ['text', 'slug', 'caller'],
        'additionalProperties': False,
    },
    'outputSchema': {
        'type': 'object',
        'properties': {
            'status': {'type': 'integer', 'description': "HTTP status from the agent endpoint (400 for invalid input here, 502/504 if the endpoint was unreachable)."},
            'log': {'type': 'string'},
            'address': {'type': ['string', 'null'], 'description': "The new session's bridge address, or null if none started."},
        },
        'required': ['status', 'log', 'address'],
    },
}


def call_tool(name, args):
    if name != 'dispatch':
        return {'content': [{'type': 'text', 'text': f'unknown tool: {name}'}], 'isError': True}
    r = dispatch(args)
    out = {'content': [{'type': 'text', 'text': json.dumps(r)}], 'structuredContent': r}
    if not 200 <= r['status'] < 300:
        out['isError'] = True
    return out


def rpc_ok(i, result):
    return {'jsonrpc': '2.0', 'id': i, 'result': result}


def rpc_err(i, code, message):
    return {'jsonrpc': '2.0', 'id': i, 'error': {'code': code, 'message': message}}


REQUEST_GUIDE = """How to make a request through dispatch.

- Name the Effort and the How task the request delivers (owner/repo#N, task id).
- State the task's desired state concretely, as something that will be true when it is done, for example "this address gets a notice at least weekly if any cluster jobs are stuck".
- Never include code, recipes, step-by-step instructions, per-task success measures or test cases. How to get there is the receiver's to decide.
- Expect pushback with one of five reasons: overly prescriptive, against governance, breaks something the receiver owns, too big for one worker, or needs other agents. Answer by restating the desired state, narrowing or splitting it, or taking it elsewhere; do not argue the method.
- Record the receiving session's address on the task once dispatch returns it."""

def handle_rpc(msg):
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or not isinstance(msg.get('method'), str):
        return rpc_err(msg.get('id') if isinstance(msg, dict) else None, -32600, 'Invalid Request')
    i, method, params = msg.get('id'), msg['method'], msg.get('params') or {}
    if method == 'initialize':
        return rpc_ok(i, {
            'protocolVersion': params.get('protocolVersion', '2025-06-18'),
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'dispatch', 'version': '2.1.0'},
            'instructions': REQUEST_GUIDE,
        })
    if method == 'ping':
        return rpc_ok(i, {})
    if method == 'tools/list':
        return rpc_ok(i, {'tools': [TOOL]})
    if method == 'tools/call':
        return rpc_ok(i, call_tool(params.get('name'), params.get('arguments')))
    if i is None:
        return None
    return rpc_err(i, -32601, f'Method not found: {method}')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def send(self, status, body=None, ctype='application/json'):
        data = b'' if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        self.send_response(status)
        if body is not None:
            self.send_header('content-type', ctype)
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/health':
            return self.send(200, {'ok': True, 'builders': list(BUILDERS), 'model': MODEL or None})
        if path == '/prompt':
            return self.send(200, PROMPT.encode(), 'text/plain; charset=utf-8')
        if re.fullmatch(r'/mcp/?', path):
            self.send_response(405)
            self.send_header('allow', 'POST')
            self.send_header('content-length', '0')
            return self.end_headers()
        return self.send(404, {'error': 'not found'})

    def do_DELETE(self):
        return self.send(200)

    def do_POST(self):
        if not re.fullmatch(r'/mcp/?', self.path.split('?', 1)[0]):
            return self.send(404, {'error': 'not found'})
        size = int(self.headers.get('content-length') or 0)
        if size > MAX_BODY:
            self.close_connection = True
            return self.send(413, rpc_err(None, -32600, 'request too large'))
        try:
            msg = json.loads(self.rfile.read(size) or b'null')
        except ValueError:
            return self.send(400, rpc_err(None, -32700, 'Parse error'))
        try:
            if isinstance(msg, list):
                out = [r for r in (handle_rpc(m) for m in msg) if r]
            else:
                out = handle_rpc(msg)
        except Exception as e:
            print(json.dumps({'ts': datetime.now(timezone.utc).isoformat(), 'event': 'internal-error', 'error': repr(e)}),
                  file=sys.stderr, flush=True)
            return self.send(500, rpc_err(msg.get('id') if isinstance(msg, dict) else None, -32603, 'Internal error'))
        return self.send(200, out) if out else self.send(202)


def serve(port=PORT):
    server = ThreadingHTTPServer(('', port), Handler)
    server.daemon_threads = True
    print(f"dispatch-mcp: listening on :{server.server_address[1]}, builders {','.join(BUILDERS)}, model {MODEL or 'none'}",
          file=sys.stderr, flush=True)
    return server


if __name__ == '__main__':
    srv = serve()
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown).start())
    srv.serve_forever()
