#!/usr/bin/env node
import http from 'node:http';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';

const PORT = Number(process.env.PORT ?? 8080);
const SCOPES_PATH = process.env.SCOPES_PATH ?? '/etc/dispatch/scopes.json';
const MODEL = process.env.MODEL ?? '';
const OPENROUTER_URL = process.env.OPENROUTER_URL ?? 'https://openrouter.ai/api/v1/chat/completions';
const MODEL_TIMEOUT_MS = Number(process.env.MODEL_TIMEOUT_MS ?? 15_000);
const DISPATCH_TIMEOUT_MS = Number(process.env.DISPATCH_TIMEOUT_MS ?? 45_000);
const MAX_BODY = 1 << 20;
const MAX_TEXT = 20_000;

const registry = JSON.parse(readFileSync(SCOPES_PATH, 'utf8'));
const FALLBACK = registry.fallback;
const BUILDERS = new Map(registry.builders.map((b) => [b.name, b]));
if (!BUILDERS.has(FALLBACK)) throw new Error(`fallback builder '${FALLBACK}' is not in the registry`);

export function routerPrompt() {
  const lines = [
    'You route one task to exactly one builder agent. Each builder owns one repository.',
    `Pick the builder whose will-do list covers the task and whose won't-do list does not exclude it.`,
    `If no builder clearly fits, pick "${FALLBACK}".`,
    'Answer with only a JSON object: {"builder": "<name>"}.',
    '',
    'Builders:',
  ];
  for (const b of registry.builders) {
    lines.push('', `name: ${b.name} (${b.repo})`, 'will do:');
    for (const w of b.will) lines.push(`- ${w}`);
    lines.push(`won't do:`);
    for (const w of b.wont) lines.push(`- ${w}`);
  }
  return lines.join('\n');
}
const PROMPT = routerPrompt();

const KEY_PATH = process.env.KEY_PATH ?? '/secrets/openrouter/OPENROUTER_API_KEY';
function openrouterKey() {
  try { return readFileSync(KEY_PATH, 'utf8').trim(); }
  catch { return (process.env.OPENROUTER_API_KEY ?? '').trim(); }
}

export function parseChoice(content) {
  if (typeof content !== 'string') return null;
  const m = content.match(/\{[\s\S]*\}/);
  let name = null;
  if (m) { try { name = JSON.parse(m[0]).builder; } catch {} }
  if (typeof name !== 'string') {
    const bare = content.trim().replace(/^["'`]+|["'`.]+$/g, '');
    name = bare;
  }
  name = name.trim().toLowerCase();
  return BUILDERS.has(name) ? name : null;
}

async function pickBuilder(text) {
  const key = openrouterKey();
  if (!MODEL) return { pod: FALLBACK, route: 'fallback', reason: 'no model configured' };
  if (!key) return { pod: FALLBACK, route: 'fallback', reason: 'no OpenRouter key' };
  try {
    const r = await fetch(OPENROUTER_URL, {
      method: 'POST',
      headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json' },
      body: JSON.stringify({
        model: MODEL,
        temperature: 0,
        max_tokens: 50,
        messages: [{ role: 'system', content: PROMPT }, { role: 'user', content: text }],
      }),
      signal: AbortSignal.timeout(MODEL_TIMEOUT_MS),
    });
    if (!r.ok) return { pod: FALLBACK, route: 'fallback', reason: `model answered ${r.status}` };
    const doc = await r.json();
    const name = parseChoice(doc?.choices?.[0]?.message?.content);
    if (!name) return { pod: FALLBACK, route: 'fallback', reason: 'model named no known builder' };
    return { pod: name, route: 'model' };
  } catch (e) {
    return { pod: FALLBACK, route: 'fallback', reason: `model error: ${e?.name === 'TimeoutError' ? 'timeout' : e?.message ?? e}` };
  }
}

async function postDispatch(builder, payload) {
  try {
    const r = await fetch(builder.dispatch, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(DISPATCH_TIMEOUT_MS),
    });
    let doc = null;
    try { doc = await r.json(); } catch {}
    if (r.ok && typeof doc?.address === 'string' && doc.address.startsWith('bridge:')) return { ok: true, address: doc.address };
    const reason = doc?.reason ?? doc?.error ?? `endpoint answered ${r.status} with no address`;
    return { ok: false, reason: String(reason) };
  } catch (e) {
    return { ok: false, reason: `endpoint unreachable: ${e?.name === 'TimeoutError' ? 'timeout' : e?.message ?? e}` };
  }
}

const SLUG = /^[a-z0-9-]{1,48}$/;

export async function dispatch(args) {
  const { text, slug, caller } = args ?? {};
  const hops = args?.hops ?? 0;
  const log = { ts: new Date().toISOString(), event: 'dispatch', caller: typeof caller === 'string' ? caller : null };
  log.text_sha256 = createHash('sha256').update(typeof text === 'string' ? text : '').digest('hex');
  const finish = (fields) => { console.log(JSON.stringify({ ...log, ...fields })); return fields; };
  if (typeof text !== 'string' || !text.trim()) return finish({ outcome: 'invalid', pod: null, address: null, reason: 'text is required' });
  if (text.length > MAX_TEXT) return finish({ outcome: 'invalid', pod: null, address: null, reason: `text is over ${MAX_TEXT} characters` });
  if (typeof slug !== 'string' || !SLUG.test(slug)) return finish({ outcome: 'invalid', pod: null, address: null, reason: 'slug must match [a-z0-9-]{1,48}' });
  if (typeof caller !== 'string' || !caller.startsWith('bridge:') || /\s/.test(caller)) return finish({ outcome: 'invalid', pod: null, address: null, reason: "caller must be a bridge address starting 'bridge:'" });
  if (!Number.isInteger(hops) || hops < 0) return finish({ outcome: 'invalid', pod: null, address: null, reason: 'hops must be a non-negative integer' });
  const choice = await pickBuilder(text);
  const res = await postDispatch(BUILDERS.get(choice.pod), { slug, caller, text, hops });
  const base = { pod: choice.pod, route: choice.route, ...(choice.reason ? { route_reason: choice.reason } : {}) };
  if (res.ok) return finish({ outcome: 'dispatched', ...base, address: res.address });
  return finish({ outcome: 'refused', ...base, address: null, reason: res.reason });
}

const TOOL = {
  name: 'dispatch',
  description: 'Start a new session on the builder pod whose scope fits the task, and return that session\'s bridge address to message it. Text that fits no builder goes to the projects pod. Keeps no state; a retry may start a second session.',
  inputSchema: {
    type: 'object',
    properties: {
      text: { type: 'string', description: 'The task, as plain text. The new session receives it as its prompt.' },
      slug: { type: 'string', description: 'Short name for the new session, [a-z0-9-]{1,48}; it leads the session title.' },
      caller: { type: 'string', description: "The caller's own bridge address (starts 'bridge:'); the new session replies there." },
      hops: { type: 'integer', minimum: 0, description: 'How many dispatches led to the caller; 0 when a person started it.' },
    },
    required: ['text', 'slug', 'caller'],
    additionalProperties: false,
  },
};

async function callTool(name, args) {
  if (name !== 'dispatch') return { content: [{ type: 'text', text: `unknown tool: ${name}` }], isError: true };
  const r = await dispatch(args);
  const isError = r.outcome !== 'dispatched';
  return { content: [{ type: 'text', text: JSON.stringify(r) }], ...(isError ? { isError: true } : {}) };
}

const rpcOk = (id, result) => ({ jsonrpc: '2.0', id, result });
const rpcErr = (id, code, message) => ({ jsonrpc: '2.0', id: id ?? null, error: { code, message } });

async function handleRpc(msg) {
  if (!msg || msg.jsonrpc !== '2.0' || typeof msg.method !== 'string') return rpcErr(msg?.id, -32600, 'Invalid Request');
  const isNote = msg.id === undefined || msg.id === null;
  switch (msg.method) {
    case 'initialize':
      return rpcOk(msg.id, {
        protocolVersion: msg.params?.protocolVersion ?? '2025-06-18',
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: 'dispatch', version: '1.0.0' },
      });
    case 'ping':
      return rpcOk(msg.id, {});
    case 'tools/list':
      return rpcOk(msg.id, { tools: [TOOL] });
    case 'tools/call':
      return rpcOk(msg.id, await callTool(msg.params?.name, msg.params?.arguments));
    default:
      if (isNote) return null;
      return rpcErr(msg.id, -32601, `Method not found: ${msg.method}`);
  }
}

function send(res, status, body) {
  if (body === null) { res.writeHead(status); res.end(); return; }
  const s = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(s) });
  res.end(s);
}

export const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/health') return send(res, 200, { ok: true, builders: [...BUILDERS.keys()], model: MODEL || null });
  if (url.pathname === '/prompt' && req.method === 'GET') { res.writeHead(200, { 'content-type': 'text/plain; charset=utf-8' }); return res.end(PROMPT); }
  if (!/^\/mcp\/?$/.test(url.pathname)) return send(res, 404, { error: 'not found' });
  if (req.method === 'DELETE') return send(res, 200, null);
  if (req.method !== 'POST') { res.writeHead(405, { allow: 'POST' }); return res.end(); }
  let body = '', size = 0;
  req.setEncoding('utf8');
  req.on('data', (c) => { size += c.length; if (size > MAX_BODY) { send(res, 413, rpcErr(null, -32600, 'request too large')); req.destroy(); } else body += c; });
  req.on('end', async () => {
    if (res.writableEnded) return;
    let msg;
    try { msg = JSON.parse(body); } catch { return send(res, 400, rpcErr(null, -32700, 'Parse error')); }
    try {
      if (Array.isArray(msg)) {
        const out = (await Promise.all(msg.map(handleRpc))).filter(Boolean);
        return out.length ? send(res, 200, out) : send(res, 202, null);
      }
      const out = await handleRpc(msg);
      return out ? send(res, 200, out) : send(res, 202, null);
    } catch (e) {
      console.error(JSON.stringify({ ts: new Date().toISOString(), event: 'internal-error', error: String(e?.stack ?? e) }));
      return send(res, 500, rpcErr(msg?.id, -32603, 'Internal error'));
    }
  });
});

if (process.env.DISPATCH_NO_LISTEN !== '1') {
  server.listen(PORT, () => console.error(`dispatch-mcp: listening on :${PORT}, builders ${[...BUILDERS.keys()].join(',')}, model ${MODEL || 'none'}`));
  for (const sig of ['SIGTERM', 'SIGINT']) process.on(sig, () => server.close(() => process.exit(0)));
}
