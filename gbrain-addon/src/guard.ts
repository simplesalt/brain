import type { BrainEngine } from 'gbrain/engine';

export const MAX_GUARD_BODY_BYTES = 8 * 1024 * 1024;
export const REFUSED_OPTIONS = ['allow_shrink', 'force', 'allow_empty', 'drop_timeline'] as const;
export const REFUSED_TOOLS = ['delete_page', 'put_pages', 'begin_page_upload'] as const;
export const FILTERED_READ_TOOLS = ['get_page', 'search', 'query', 'list_pages', 'resolve_slugs', 'get_backlinks', 'find_experts', 'chronicle_day'] as const;

export interface StoredPage {
  bytes: number;
  revision: string | null;
}

export interface MarkInput {
  kind: 'delete' | 'replace';
  slug: string;
  sourceId: string;
  reason: string;
  content: string | null;
  principal: string;
  page: StoredPage;
}

export interface GuardStore {
  page(slug: string, sourceId: string): Promise<StoredPage | null>;
  mark(input: MarkInput): Promise<{ id: number; replaced: number }>;
  withdraw(slug: string, sourceId: string, principal: string): Promise<number>;
  flagged(): Promise<Set<string>>;
}

export type Decision =
  | { action: 'forward'; args?: Record<string, unknown>; filter?: boolean }
  | { action: 'refuse'; code: string; message: string }
  | { action: 'answer'; text: string };

const refuse = (code: string, message: string): Decision => ({ action: 'refuse', code, message });
const str = (v: unknown) => (typeof v === 'string' && v.length > 0 ? v : null);
export const flagKey = (sourceId: string, slug: string) => `${sourceId}\u0000${slug}`;

export async function decide(name: string, args: Record<string, unknown>, store: GuardStore, defaultSource: string, principal: string, overwritePrefixes: readonly string[] = []): Promise<Decision> {
  const slug = str(args.slug);
  const sourceId = str(args.source_id) ?? defaultSource;

  if ((REFUSED_TOOLS as readonly string[]).includes(name)) {
    return refuse('guard_tool_refused',
      `${name} is not available to Hermes. Nothing was written. To remove a page, call mark_page_for_deletion with the slug and a reason: the page is hidden at once and deleted after 6 months unless the mark is withdrawn. To change a page, use edit_page.`);
  }

  if ((FILTERED_READ_TOOLS as readonly string[]).includes(name)) {
    const { include_deleted, ...rest } = args;
    if (include_deleted === true) return { action: 'forward', args: rest, filter: false };
    if (name === 'get_page' && slug && (await store.flagged()).has(flagKey(sourceId, slug))) {
      return refuse('page_flagged_for_deletion',
        `${slug} is marked for deletion and hidden. Pass include_deleted: true to read it, or withdraw_page_mark to restore it.`);
    }
    return { action: 'forward', args: rest, filter: true };
  }

  if (name === 'put_page') {
    const used = REFUSED_OPTIONS.filter(o => args[o] !== undefined && args[o] !== false);
    if (used.length) {
      return refuse('guard_option_refused',
        `put_page option ${used.join(', ')} is not available to Hermes. Nothing was written. Change an existing page with edit_page; to replace one completely, call propose_page_replacement.`);
    }
    if (!slug) return { action: 'forward' };
    const page = await store.page(slug, sourceId);
    if (page && !overwritePrefixes.some(p => slug.startsWith(p))) {
      return refuse('guard_overwrite_refused',
        `${slug} already exists (${page.bytes} bytes), and put_page would replace all of it. Nothing was written. Change it with edit_page, or, if it really must be replaced completely, call propose_page_replacement with the new content and a reason.`);
    }
    if ((await store.flagged()).has(flagKey(sourceId, slug))) {
      return refuse('page_flagged_for_deletion', `${slug} is marked for deletion. Withdraw the mark with withdraw_page_mark before writing it again. Nothing was written.`);
    }
    return { action: 'forward' };
  }

  if (name === 'mark_page_for_deletion' || name === 'propose_page_replacement') {
    const reason = str(args.reason);
    if (!slug || !reason) return refuse('guard_mark_invalid', `${name} needs slug and reason. Nothing was recorded.`);
    const content = name === 'propose_page_replacement' ? str(args.content) : null;
    if (name === 'propose_page_replacement' && content === null) return refuse('guard_mark_invalid', 'propose_page_replacement needs the complete new content. Nothing was recorded.');
    const page = await store.page(slug, sourceId);
    if (!page) return refuse('guard_mark_invalid', `${slug} does not exist in source ${sourceId}. Nothing was recorded.`);
    const kind = name === 'mark_page_for_deletion' ? 'delete' : 'replace';
    const { id, replaced } = await store.mark({ kind, slug, sourceId, reason, content, principal, page });
    return {
      action: 'answer',
      text: JSON.stringify({
        status: 'marked', mark_id: id, kind, slug, source_id: sourceId, page_bytes: page.bytes, replaced_earlier_marks: replaced,
        note: kind === 'delete'
          ? 'The page is now hidden from reads and searches (include_deleted: true still shows it). It is deleted after 6 months unless the mark is withdrawn with withdraw_page_mark.'
          : 'The page is unchanged. A reviewed job applies the replacement later, only if the page has not changed since; withdraw it with withdraw_page_mark.',
      }),
    };
  }

  if (name === 'withdraw_page_mark') {
    if (!slug) return refuse('guard_mark_invalid', 'withdraw_page_mark needs slug. Nothing was changed.');
    const n = await store.withdraw(slug, sourceId, principal);
    return { action: 'answer', text: JSON.stringify({ status: 'withdrawn', slug, source_id: sourceId, withdrawn: n }) };
  }

  return { action: 'forward' };
}

export function filterFlagged(value: unknown, flagged: Set<string>, defaultSource: string): unknown {
  if (Array.isArray(value)) {
    return value
      .filter(v => !(v && typeof v === 'object' && typeof (v as Record<string, unknown>).slug === 'string'
        && flagged.has(flagKey(String((v as Record<string, unknown>).source_id ?? defaultSource), String((v as Record<string, unknown>).slug)))))
      .map(v => filterFlagged(v, flagged, defaultSource));
  }
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) out[k] = filterFlagged(v, flagged, defaultSource);
    return out;
  }
  return value;
}

const INCLUDE_DELETED = { type: 'boolean', description: 'Also show pages marked for deletion.' };

export const GUARD_TOOL_DEFS = [
  {
    name: 'mark_page_for_deletion',
    description: 'Delete an existing page the safe way: it is hidden from reads and searches at once and deleted after 6 months unless the mark is withdrawn with withdraw_page_mark.',
    inputSchema: {
      type: 'object',
      properties: {
        slug: { type: 'string', description: 'Page slug.' },
        reason: { type: 'string', description: 'Why the page should go.' },
        source_id: { type: 'string', description: 'Source (default: default).' },
      },
      required: ['slug', 'reason'],
    },
  },
  {
    name: 'propose_page_replacement',
    description: 'Ask for an existing page to be replaced by new complete content. The page is not changed now; a reviewed job applies it later, only if the page has not changed since. Use edit_page for ordinary changes.',
    inputSchema: {
      type: 'object',
      properties: {
        slug: { type: 'string', description: 'Page slug.' },
        content: { type: 'string', description: 'Complete new markdown, frontmatter included.' },
        reason: { type: 'string', description: 'Why the page should be replaced.' },
        source_id: { type: 'string', description: 'Source (default: default).' },
      },
      required: ['slug', 'content', 'reason'],
    },
  },
  {
    name: 'withdraw_page_mark',
    description: 'Withdraw every open deletion mark or replacement proposal on a page; a page hidden for deletion becomes visible again.',
    inputSchema: {
      type: 'object',
      properties: {
        slug: { type: 'string', description: 'Page slug.' },
        source_id: { type: 'string', description: 'Source (default: default).' },
      },
      required: ['slug'],
    },
  },
];

export async function ensureGuardTables(engine: Pick<BrainEngine, 'executeRaw'>): Promise<void> {
  await engine.executeRaw(`CREATE TABLE IF NOT EXISTS addon_page_marks (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('delete', 'replace')),
    slug TEXT NOT NULL,
    source_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    content TEXT,
    principal TEXT NOT NULL,
    page_bytes BIGINT NOT NULL,
    page_revision TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    withdrawn_at TIMESTAMPTZ,
    applied_at TIMESTAMPTZ,
    outcome TEXT
  )`);
  await engine.executeRaw(`CREATE INDEX IF NOT EXISTS addon_page_marks_open ON addon_page_marks (source_id, slug) WHERE withdrawn_at IS NULL AND applied_at IS NULL`);
}

export const ensureMarkTable = ensureGuardTables;

export function engineStore(engine: Pick<BrainEngine, 'executeRaw'>, flagTtlMs = 5000): GuardStore {
  let cache: { at: number; set: Set<string> } | null = null;
  const invalidate = () => { cache = null; };
  return {
    async page(slug, sourceId) {
      const rows = await engine.executeRaw<{ bytes: unknown; revision: unknown }>(
        `SELECT CASE WHEN to_jsonb(p)->>'body_storage' = 'segmented' THEN (to_jsonb(p)->>'body_bytes')::bigint
                     ELSE COALESCE(octet_length(p.compiled_truth), 0) + COALESCE(octet_length(p.timeline), 0) END AS bytes,
                to_jsonb(p)->>'knowledge_revision' AS revision
           FROM pages p WHERE p.slug = $1 AND p.source_id = $2 AND p.deleted_at IS NULL`, [slug, sourceId]);
      if (!rows.length) return null;
      return { bytes: Number(rows[0].bytes ?? 0), revision: rows[0].revision == null ? null : String(rows[0].revision) };
    },
    async mark(m) {
      invalidate();
      const done = await engine.executeRaw<{ n: number }>(
        `WITH w AS (UPDATE addon_page_marks SET withdrawn_at = now(), outcome = 'superseded'
           WHERE slug = $1 AND source_id = $2 AND withdrawn_at IS NULL AND applied_at IS NULL RETURNING 1)
         SELECT count(*)::int AS n FROM w`, [m.slug, m.sourceId]);
      const rows = await engine.executeRaw<{ id: unknown }>(
        `INSERT INTO addon_page_marks (kind, slug, source_id, reason, content, principal, page_bytes, page_revision)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id`,
        [m.kind, m.slug, m.sourceId, m.reason, m.content, m.principal, m.page.bytes, m.page.revision]);
      return { id: Number(rows[0].id), replaced: Number(done[0]?.n ?? 0) };
    },
    async withdraw(slug, sourceId) {
      invalidate();
      const rows = await engine.executeRaw<{ n: number }>(
        `WITH w AS (UPDATE addon_page_marks SET withdrawn_at = now(), outcome = 'withdrawn'
           WHERE slug = $1 AND source_id = $2 AND withdrawn_at IS NULL AND applied_at IS NULL RETURNING 1)
         SELECT count(*)::int AS n FROM w`, [slug, sourceId]);
      return Number(rows[0]?.n ?? 0);
    },
    async flagged() {
      if (cache && Date.now() - cache.at < flagTtlMs) return cache.set;
      const rows = await engine.executeRaw<{ slug: string; source_id: string }>(
        `SELECT slug, source_id FROM addon_page_marks WHERE kind = 'delete' AND withdrawn_at IS NULL AND applied_at IS NULL`);
      cache = { at: Date.now(), set: new Set(rows.map(r => flagKey(r.source_id, r.slug))) };
      return cache.set;
    },
  };
}

export interface GuardOpts {
  upstream: string;
  defaultSource: string;
  overwritePrefixes?: readonly string[];
  fetchImpl?: typeof fetch;
  log?: (entry: Record<string, unknown>) => void;
}

const rpcJson = (body: unknown, extra: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json', ...extra } });

function principalOf(auth: string | null): string {
  if (!auth) return 'anonymous';
  const t = auth.replace(/^Bearer\s+/i, '');
  return `bearer:${new Bun.CryptoHasher('sha256').update(t).digest('hex').slice(0, 12)}`;
}

export function parseRpcBody(text: string): any {
  const t = text.trim();
  if (t.startsWith('{') || t.startsWith('[')) return JSON.parse(t);
  let last: any = null;
  for (const block of text.split(/\r?\n\r?\n/)) {
    const data = block.split(/\r?\n/).filter(l => l.startsWith('data:')).map(l => l.slice(5).trim()).join('\n');
    if (!data) continue;
    try { const o = JSON.parse(data); if (o && (o.result !== undefined || o.error !== undefined)) last = o; } catch { /* keep scanning */ }
  }
  return last;
}

function toolText(msg: any): string | null {
  const content = msg?.result?.content;
  if (!Array.isArray(content)) return null;
  return content.map((c: any) => (typeof c?.text === 'string' ? c.text : '')).join('\n');
}

export function makeGuardHandler(store: GuardStore, opts: GuardOpts) {
  const doFetch = opts.fetchImpl ?? fetch;
  const log = opts.log ?? ((e) => console.log(JSON.stringify({ guard: true, ...e })));
  const passHeaders = (req: Request) => {
    const h = new Headers();
    for (const k of ['authorization', 'content-type', 'accept', 'mcp-session-id', 'mcp-protocol-version', 'last-event-id']) {
      const v = req.headers.get(k);
      if (v) h.set(k, v);
    }
    return h;
  };
  const forward = async (req: Request, body?: string) => {
    const res = await doFetch(opts.upstream, { method: req.method, headers: passHeaders(req), body: body ?? (req.method === 'GET' || req.method === 'DELETE' ? undefined : await req.text()) });
    const h = new Headers();
    res.headers.forEach((v, k) => { if (!['content-length', 'transfer-encoding', 'connection'].includes(k)) h.set(k, v); });
    return new Response(res.body, { status: res.status, headers: h });
  };
  const call = async (req: Request, body: unknown) => {
    const res = await doFetch(opts.upstream, { method: 'POST', headers: passHeaders(req), body: JSON.stringify(body) });
    const raw = await res.text();
    return { res, raw, parsed: res.ok ? parseRpcBody(raw) : null };
  };
  const toolError = (id: unknown, code: string, message: string, extra: Record<string, string>) =>
    rpcJson({ jsonrpc: '2.0', id, result: { isError: true, content: [{ type: 'text', text: JSON.stringify({ error: code, message }) }] } }, extra);

  return async (req: Request): Promise<Response> => {
    const path = new URL(req.url).pathname;
    if (path === '/health') return new Response(JSON.stringify({ ok: true }), { headers: { 'content-type': 'application/json' } });
    if (path !== '/mcp') return new Response(JSON.stringify({ error: 'not found' }), { status: 404, headers: { 'content-type': 'application/json' } });
    if (req.method !== 'POST') return forward(req);

    const text = await req.text();
    if (Buffer.byteLength(text) > MAX_GUARD_BODY_BYTES) return new Response(JSON.stringify({ error: 'body too large' }), { status: 413 });
    let msg: any;
    try { msg = JSON.parse(text); } catch { return forward(req, text); }
    if (Array.isArray(msg)) {
      const risky = msg.some((m: any) => m?.method === 'tools/call' || m?.method === 'tools/list');
      if (risky) return rpcJson(msg.filter((m: any) => m?.id !== undefined).map((m: any) => ({ jsonrpc: '2.0', id: m.id, error: { code: -32600, message: 'guard: batched tool calls are not accepted; send one request at a time' } })));
      return forward(req, text);
    }
    const sessionHeader = req.headers.get('mcp-session-id');
    const extra: Record<string, string> = sessionHeader ? { 'mcp-session-id': sessionHeader } : {};

    if (msg?.method === 'tools/list') {
      const { res, raw, parsed } = await call(req, msg);
      if (!parsed?.result || !Array.isArray(parsed.result.tools)) {
        return new Response(raw, { status: res.status, headers: { 'content-type': res.headers.get('content-type') ?? 'application/json', ...extra } });
      }
      const hidden = new Set<string>(REFUSED_TOOLS);
      const filtered = new Set<string>(FILTERED_READ_TOOLS);
      parsed.result.tools = [
        ...parsed.result.tools.filter((t: any) => !hidden.has(t?.name)).map((t: any) => {
          if (!filtered.has(t?.name) || !t.inputSchema || typeof t.inputSchema !== 'object') return t;
          return { ...t, inputSchema: { ...t.inputSchema, properties: { ...(t.inputSchema.properties ?? {}), include_deleted: INCLUDE_DELETED } } };
        }),
        ...GUARD_TOOL_DEFS,
      ];
      const sid = res.headers.get('mcp-session-id');
      return rpcJson(parsed, sid ? { 'mcp-session-id': sid } : extra);
    }

    if (msg?.method !== 'tools/call') return forward(req, text);

    const name = String(msg.params?.name ?? '');
    const args = (msg.params?.arguments && typeof msg.params.arguments === 'object') ? msg.params.arguments : {};
    const principal = principalOf(req.headers.get('authorization'));
    const sourceId = (typeof args.source_id === 'string' && args.source_id) || opts.defaultSource;
    let d: Decision;
    try {
      d = await decide(name, args, store, opts.defaultSource, principal, opts.overwritePrefixes ?? []);
    } catch (e) {
      log({ tool: name, slug: args.slug ?? null, decision: 'error', error: e instanceof Error ? e.message : String(e) });
      return toolError(msg.id, 'guard_unavailable', 'The write guard could not check this call. Nothing was written. Try again later.', extra);
    }
    if (d.action === 'refuse') {
      log({ tool: name, slug: args.slug ?? null, source_id: sourceId, decision: 'refuse', code: d.code, principal });
      return toolError(msg.id, d.code, d.message, extra);
    }
    if (d.action === 'answer') {
      log({ tool: name, slug: args.slug ?? null, source_id: sourceId, decision: 'answer', principal });
      return rpcJson({ jsonrpc: '2.0', id: msg.id, result: { content: [{ type: 'text', text: d.text }] } }, extra);
    }

    const outBody = d.args ? JSON.stringify({ ...msg, params: { ...msg.params, arguments: d.args } }) : text;
    if (!d.filter) return forward(req, outBody);

    const flagged = await store.flagged();
    const res = await doFetch(opts.upstream, { method: 'POST', headers: passHeaders(req), body: outBody });
    const raw = await res.text();
    const parsed = res.ok ? parseRpcBody(raw) : null;
    const t = toolText(parsed);
    if (!parsed || t === null || flagged.size === 0) {
      return new Response(raw, { status: res.status, headers: { 'content-type': res.headers.get('content-type') ?? 'application/json', ...extra } });
    }
    parsed.result.content = parsed.result.content.map((c: any) => {
      if (typeof c?.text !== 'string') return c;
      let inner: unknown;
      try { inner = JSON.parse(c.text); } catch { return c; }
      return { ...c, text: JSON.stringify(filterFlagged(inner, flagged, opts.defaultSource)) };
    });
    if (parsed.result.structuredContent !== undefined) parsed.result.structuredContent = filterFlagged(parsed.result.structuredContent, flagged, opts.defaultSource);
    return rpcJson(parsed, extra);
  };
}
