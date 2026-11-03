#!/usr/bin/env node
// Cluster-local Effort MCP server (simplesalt/projects#865).
// Runs inside the builder image and executes the SAME Effort scripts the builder sessions
// run (/etc/claude-code/scripts), so every schema rule, refusal message and status
// transition is the scripts' own. This file only maps MCP tool calls onto script argv
// (no shell) and returns stdout, or stderr as an isError result on a non-zero exit.
//
// Endpoints (Streamable HTTP, stateless, JSON responses):
//   POST /mcp/coordinator  find, get, create, select, release, update, depend
//   POST /mcp/doer         find, get
//   GET  /health

import http from 'node:http';
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, chmodSync } from 'node:fs';
import { join } from 'node:path';

const PORT = Number(process.env.PORT ?? 8080);
const SCRIPTS = process.env.SCRIPTS_DIR ?? '/etc/claude-code/scripts';
const STATE = process.env.HOME ?? '/state';
const DEFAULT_HOST = process.env.HOLDER_HOST ?? 'efforts';
const TIMEOUT_MS = Number(process.env.SCRIPT_TIMEOUT_MS ?? 120_000);
const MAX_BODY = 1 << 20;

// The scripts build the In Use holder as "$(hostname)/$CLAUDE_CODE_SESSION_ID". A shim
// `hostname` first on PATH prints the caller's host part, so the stored holder is exactly
// the caller's session label "<host>/<session>".
const SHIM_DIR = join(STATE, 'bin');
mkdirSync(SHIM_DIR, { recursive: true });
mkdirSync(join(STATE, '.claude', 'selected-efforts.d'), { recursive: true });
writeFileSync(join(SHIM_DIR, 'hostname'), '#!/bin/sh\nprintf "%s\\n" "${EFFORT_HOLDER_HOST:?}"\n');
chmodSync(join(SHIM_DIR, 'hostname'), 0o755);

function holderParts(label) {
  if (typeof label !== 'string' || !label.trim()) throw new UserError('error: session is required — pass the session label that will be stored as In Use');
  const s = label.trim();
  if (/[\s]/.test(s)) throw new UserError(`error: session '${s}' must not contain whitespace`);
  const i = s.indexOf('/');
  if (i === -1) return { host: DEFAULT_HOST, session: s };
  const host = s.slice(0, i), session = s.slice(i + 1);
  if (!host || !session) throw new UserError(`error: session '${s}' must be <session> or <host>/<session>`);
  return { host, session };
}

class UserError extends Error {}

function run(script, args, label) {
  const env = {
    PATH: `${SHIM_DIR}:${process.env.PATH ?? '/usr/local/bin:/usr/bin:/bin'}`,
    HOME: STATE,
    GH_TOKEN: process.env.GH_TOKEN ?? process.env.GITHUB_TOKEN ?? '',
    GH_PROMPT_DISABLED: '1',
    GH_NO_UPDATE_NOTIFIER: '1',
    LANG: 'C.UTF-8',
    LC_ALL: 'C.UTF-8',
    EFFORT_HOLDER_HOST: DEFAULT_HOST,
  };
  if (label !== undefined) {
    const { host, session } = holderParts(label);
    env.EFFORT_HOLDER_HOST = host;
    env.CLAUDE_CODE_SESSION_ID = session;
  }
  return new Promise((resolve) => {
    const child = spawn(join(SCRIPTS, script), args, { env, cwd: STATE, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '', err = '';
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
    const timer = setTimeout(() => { err += `\nerror: ${script} timed out after ${TIMEOUT_MS} ms`; child.kill('SIGKILL'); }, TIMEOUT_MS);
    child.on('error', (e) => { clearTimeout(timer); resolve({ code: 127, out, err: `${err}error: could not run ${script}: ${e.message}` }); });
    child.on('close', (code) => { clearTimeout(timer); resolve({ code: code ?? 1, out, err }); });
  });
}

// Writes go one at a time: the In Use lock is read-then-write in the scripts, and the
// board-cache file is shared, so serialising here removes races between this server's callers.
let writeChain = Promise.resolve();
function serial(fn) {
  const p = writeChain.then(fn, fn);
  writeChain = p.catch(() => {});
  return p;
}

const str = (v) => (v === undefined || v === null ? undefined : String(v));
const list = (v) => (v === undefined || v === null ? [] : Array.isArray(v) ? v : [v]);
function need(a, k) {
  const v = a[k];
  if (v === undefined || v === null || String(v) === '') throw new UserError(`error: ${k} is required`);
  return String(v);
}
function idOf(v, what) {
  if (v && typeof v === 'object') v = v.id;
  if (v === undefined || v === null || String(v) === '') throw new UserError(`error: ${what} needs an id`);
  return String(v);
}
function closeArgs(flag, v) {
  const out = [flag, idOf(v, flag.slice(2))];
  if (v && typeof v === 'object' && v.note !== undefined && v.note !== null && String(v.note) !== '') out.push('--note', String(v.note));
  return out;
}

const S = { type: 'string' };
const CLOSE = { anyOf: [S, { type: 'integer' }, { type: 'object', properties: { id: { anyOf: [S, { type: 'integer' }] }, note: S }, required: ['id'] }] };
const SESSION = { type: 'string', description: 'Caller session label, stored as the In Use holder ("<host>/<session>"; a bare label is stored as "efforts/<label>"). Use the same label to select, update and release.' };
const REF = { type: 'string', description: 'owner/repo#N or #N' };
const STATUSES = ['Planning', 'Doing', 'Blocked', 'Review', 'Done', 'Paused', 'Deferred', 'Cancelled'];

const TOOLS = {
  effort_find: {
    description: 'Find open Efforts on the SimpleSalt Delivery board related to a Why/What/Where (find-effort).',
    inputSchema: { type: 'object', properties: { why: S, what: S, where: S, limit: { type: 'integer' } } },
    write: false,
    argv: (a) => {
      const v = ['find-effort.sh'];
      for (const k of ['why', 'what', 'where', 'limit']) if (str(a[k]) !== undefined && str(a[k]) !== '') v.push(`--${k}`, str(a[k]));
      return v;
    },
  },
  effort_get: {
    description: 'Read one Effort record: status, assigned, In Use, parent, blockers, body, children (get-effort).',
    inputSchema: { type: 'object', properties: { ref: REF }, required: ['ref'] },
    write: false,
    argv: (a) => ['get-effort.sh', need(a, 'ref')],
  },
  effort_create: {
    description: 'Create an Effort on the board, schema- and gate-checked (create-effort).',
    inputSchema: {
      type: 'object',
      properties: {
        title: S, type: { type: 'string', enum: ['Feature', 'Bug', 'Analysis', 'Advisory'] }, assigned: S,
        why: S, what: S, indicators: S, where: S, gaps: S, facts: S, unknowns: S, how: S, tags: S,
        parent: { type: 'string', description: 'Parent ref, or "none" for no parent; omitted = auto-fit' },
        blockers: { type: 'array', items: S }, status: { type: 'string', enum: ['Planning', 'Deferred'] },
        dry_run: { type: 'boolean' },
      },
      required: ['title', 'type', 'assigned', 'why', 'what', 'indicators', 'where'],
    },
    write: true,
    argv: (a) => {
      const v = ['create-effort.sh'];
      for (const k of ['title', 'type', 'assigned', 'why', 'what', 'indicators', 'where', 'gaps', 'facts', 'unknowns', 'how', 'tags', 'status']) {
        const s = str(a[k]);
        if (s !== undefined && s !== '') v.push(`--${k}`, s);
      }
      // --tags is required by the script; the connector contract makes it optional.
      if (!v.includes('--tags')) v.push('--tags', 'none');
      const p = str(a.parent);
      if (p !== undefined && p !== '') { if (p.toLowerCase() === 'none') v.push('--no-parent'); else v.push('--parent', p); }
      const b = list(a.blockers).map(String).filter((x) => x.trim());
      if (b.length) v.push('--blockers', b.join(','));
      if (a.dry_run === true) v.push('--dry-run');
      return v;
    },
  },
  effort_select: {
    description: 'Reserve an Effort for a session by writing its In Use field (select-effort). Refused if anyone holds it.',
    inputSchema: { type: 'object', properties: { ref: REF, session: SESSION }, required: ['ref', 'session'] },
    write: true,
    argv: (a) => ['select-effort.sh', need(a, 'ref')],
  },
  effort_release: {
    description: "Release a session's reservation of an Effort (release-effort). Refused if another session holds it.",
    inputSchema: { type: 'object', properties: { ref: REF, session: SESSION }, required: ['ref', 'session'] },
    write: true,
    argv: (a) => ['release-effort.sh', need(a, 'ref')],
  },
  effort_update: {
    description: 'Update an Effort the session holds: status (gated, force overrides), assigned, and item operations, run in the order given (update-effort).',
    inputSchema: {
      type: 'object',
      properties: {
        ref: REF, session: SESSION,
        status: { type: 'string', enum: STATUSES }, force: { type: 'boolean' }, assigned: S, reconcile: { type: 'boolean' },
        add_gap: { anyOf: [S, { type: 'array', items: S }] }, resolve_gap: CLOSE, cancel_gap: CLOSE,
        add_unknown: { anyOf: [S, { type: 'array', items: S }] }, resolve_unknown: CLOSE, cancel_unknown: CLOSE,
        add_fact: { type: 'object', properties: { text: S, evidence: S }, required: ['text'] },
        add_indicator: { anyOf: [S, { type: 'array', items: S }] },
        add_task: { type: 'object', properties: { text: S, blockers: { anyOf: [S, { type: 'array', items: S }] }, indicators: S }, required: ['text'] },
        check_task: { anyOf: [S, { type: 'integer' }] }, cancel_task: CLOSE,
      },
      required: ['ref', 'session'],
    },
    write: true,
    argv: (a) => {
      const v = ['update-effort.sh', need(a, 'ref')];
      // Item operations follow the order the caller's keys arrive in; status and assigned
      // are position-independent in the script.
      for (const [k, val] of Object.entries(a)) {
        if (val === undefined || val === null) continue;
        switch (k) {
          case 'status': v.push('--status', String(val)); break;
          case 'force': if (val === true) v.push('--force'); break;
          case 'reconcile': if (val === true) v.push('--reconcile'); break;
          case 'assigned': v.push('--assigned', String(val)); break;
          case 'add_gap': case 'add_unknown': case 'add_indicator':
            for (const t of list(val)) v.push(`--${k.replace('_', '-')}`, String(t));
            break;
          case 'resolve_gap': case 'cancel_gap': case 'resolve_unknown': case 'cancel_unknown': case 'cancel_task':
            for (const t of list(val)) v.push(...closeArgs(`--${k.replace('_', '-')}`, t));
            break;
          case 'check_task':
            for (const t of list(val)) v.push('--check-task', idOf(t, 'check-task'));
            break;
          case 'add_fact':
            for (const f of list(val)) {
              const t = typeof f === 'object' ? f.text : f;
              v.push('--add-fact', String(t ?? ''));
              if (typeof f === 'object' && f.evidence !== undefined && f.evidence !== null && String(f.evidence) !== '') v.push('--evidence', String(f.evidence));
            }
            break;
          case 'add_task':
            for (const t of list(val)) {
              const o = typeof t === 'object' ? t : { text: t };
              v.push('--add-task', String(o.text ?? ''));
              const b = Array.isArray(o.blockers) ? o.blockers.join(',') : str(o.blockers);
              if (b !== undefined && b !== '') v.push('--task-blockers', b);
              if (str(o.indicators) !== undefined && str(o.indicators) !== '') v.push('--task-indicators', str(o.indicators));
            }
            break;
          case 'ref': case 'session': break;
          default: throw new UserError(`error: unknown argument '${k}'`);
        }
      }
      return v;
    },
  },
  effort_depend: {
    description: 'Add or remove a blocked-by edge between two Efforts (project-depend), with its refusals; reconciles the blocked Effort when the session holds it.',
    inputSchema: {
      type: 'object',
      properties: { ref: { type: 'string', description: 'The Effort that is blocked' }, blocker: { type: 'string', description: 'The Effort that blocks it' }, action: { type: 'string', enum: ['add', 'remove'] }, session: SESSION },
      required: ['ref', 'blocker', 'action', 'session'],
    },
    write: true,
    argv: (a) => {
      const act = need(a, 'action');
      if (act !== 'add' && act !== 'remove') throw new UserError(`error: unknown subcommand '${act}'`);
      return ['project-depend.sh', act, need(a, 'ref'), '--blocked-by', need(a, 'blocker')];
    },
  },
};

const ROLES = {
  coordinator: Object.keys(TOOLS),
  doer: ['effort_find', 'effort_get'],
};

async function callTool(role, name, args) {
  if (!ROLES[role].includes(name)) return errResult(`error: tool '${name}' is not available on the ${role} endpoint`);
  const t = TOOLS[name];
  const a = args && typeof args === 'object' ? args : {};
  const held = (t.inputSchema.required ?? []).includes('session');
  let argv;
  try {
    argv = t.argv(a);
    if (held) holderParts(a.session);
  } catch (e) {
    if (e instanceof UserError) return errResult(e.message);
    throw e;
  }
  const exec = () => run(argv[0], argv.slice(1), held ? a.session : undefined);
  const r = t.write ? await serial(exec) : await exec();
  const out = r.out.trim(), err = r.err.trim();
  console.log(JSON.stringify({ at: new Date().toISOString(), role, tool: name, session: held ? a.session : undefined, ref: a.ref, code: r.code }));
  if (r.code !== 0) return errResult([err, out].filter(Boolean).join('\n') || `error: ${argv[0]} exited ${r.code}`);
  const content = [{ type: 'text', text: out || '{}' }];
  if (err) content.push({ type: 'text', text: err });
  return { content };
}
const errResult = (text) => ({ content: [{ type: 'text', text }], isError: true });

const rpcOk = (id, result) => ({ jsonrpc: '2.0', id, result });
const rpcErr = (id, code, message) => ({ jsonrpc: '2.0', id: id ?? null, error: { code, message } });

async function handleRpc(role, msg) {
  if (!msg || msg.jsonrpc !== '2.0' || typeof msg.method !== 'string') return rpcErr(msg?.id, -32600, 'Invalid Request');
  const isNote = msg.id === undefined || msg.id === null;
  switch (msg.method) {
    case 'initialize':
      return rpcOk(msg.id, {
        protocolVersion: msg.params?.protocolVersion ?? '2025-06-18',
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: `efforts-${role}`, version: '1.0.0' },
      });
    case 'ping':
      return rpcOk(msg.id, {});
    case 'tools/list':
      return rpcOk(msg.id, { tools: ROLES[role].map((n) => ({ name: n, description: TOOLS[n].description, inputSchema: TOOLS[n].inputSchema })) });
    case 'tools/call':
      return rpcOk(msg.id, await callTool(role, msg.params?.name, msg.params?.arguments));
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

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/health') return send(res, 200, { ok: true });
  const m = /^\/mcp\/(coordinator|doer)\/?$/.exec(url.pathname);
  if (!m) return send(res, 404, { error: 'not found' });
  const role = m[1];
  if (req.method === 'GET') { res.writeHead(405, { allow: 'POST' }); return res.end(); }
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
        const out = (await Promise.all(msg.map((x) => handleRpc(role, x)))).filter(Boolean);
        return out.length ? send(res, 200, out) : send(res, 202, null);
      }
      const out = await handleRpc(role, msg);
      return out ? send(res, 200, out) : send(res, 202, null);
    } catch (e) {
      console.error('effort-mcp: internal error:', e?.stack ?? e);
      return send(res, 500, rpcErr(msg?.id, -32603, 'Internal error'));
    }
  });
});

server.listen(PORT, () => console.log(`effort-mcp: listening on :${PORT}, scripts ${SCRIPTS}`));
for (const sig of ['SIGTERM', 'SIGINT']) process.on(sig, () => server.close(() => process.exit(0)));
