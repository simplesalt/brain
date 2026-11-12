import type { BrainEngine } from 'gbrain/engine';

export const REMOVE_REFUSE_FRACTION = 0.8;
export const NEAR_EMPTY_BYTES = 64;
export const MAX_GUARD_BODY_BYTES = 8 * 1024 * 1024;
export const REFUSED_OPTIONS = ['allow_shrink', 'force', 'allow_empty', 'drop_timeline'] as const;
export const REFUSED_TOOLS = ['delete_page', 'put_pages', 'begin_page_upload'] as const;
export const MARK_TOOLS = ['mark_page_for_deletion', 'propose_page_replacement', 'withdraw_page_mark'] as const;

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
}

export type Decision =
  | { action: 'forward' }
  | { action: 'refuse'; code: string; message: string }
  | { action: 'answer'; text: string };

const refuse = (code: string, message: string): Decision => ({ action: 'refuse', code, message });
const str = (v: unknown) => (typeof v === 'string' && v.length > 0 ? v : null);

export async function decide(name: string, args: Record<string, unknown>, store: GuardStore, defaultSource: string, principal: string): Promise<Decision> {
  const slug = str(args.slug);
  const sourceId = str(args.source_id) ?? defaultSource;

  if ((REFUSED_TOOLS as readonly string[]).includes(name)) {
    return refuse('guard_tool_refused',
      `${name} is not available to Hermes. Nothing was written. To remove a page, call mark_page_for_deletion with the slug and a reason; a reviewed job deletes it after 7 days unless the mark is withdrawn. To change a page, use edit_page or append_page.`);
  }

  if (name === 'put_page') {
    const used = REFUSED_OPTIONS.filter(o => args[o] !== undefined && args[o] !== false);
    if (used.length) {
      return refuse('guard_option_refused',
        `put_page option ${used.join(', ')} is not available to Hermes. Nothing was written. Change an existing page with edit_page or append_page; to replace one completely, call propose_page_replacement.`);
    }
    if (!slug) return { action: 'forward' };
    const page = await store.page(slug, sourceId);
    if (page) {
      return refuse('guard_overwrite_refused',
        `${slug} already exists (${page.bytes} bytes), and put_page would replace all of it. Nothing was written. Change part of it with edit_page, add to it with append_page, or, if it really must be replaced completely, call propose_page_replacement with the new content and a reason; a reviewed job applies it after 7 days.`);
    }
    return { action: 'forward' };
  }

  if (name === 'edit_page') {
    if (!slug || !Array.isArray(args.edits)) return { action: 'forward' };
    const page = await store.page(slug, sourceId);
    if (!page || page.bytes <= 0) return { action: 'forward' };
    let removed = 0;
    for (const e of args.edits as Array<Record<string, unknown>>) {
      const oldText = typeof e?.old_text === 'string' ? e.old_text : '';
      const newText = typeof e?.new_text === 'string' ? e.new_text : '';
      removed += Buffer.byteLength(oldText, 'utf8') - Buffer.byteLength(newText, 'utf8');
    }
    if (removed <= 0) return { action: 'forward' };
    const left = page.bytes - removed;
    if (removed > page.bytes * REMOVE_REFUSE_FRACTION || (left < NEAR_EMPTY_BYTES && page.bytes >= NEAR_EMPTY_BYTES)) {
      return refuse('guard_gutting_refused',
        `This edit would remove ${removed} of ${slug}'s ${page.bytes} bytes, leaving about ${Math.max(left, 0)}. Edits may not remove more than ${Math.round(REMOVE_REFUSE_FRACTION * 100)}% of a page or leave it nearly empty. Nothing was written. Make a smaller edit, or call propose_page_replacement (or mark_page_for_deletion) with a reason; a reviewed job applies it after 7 days.`);
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
        note: `The page is unchanged. A reviewed job applies this ${kind === 'delete' ? 'deletion' : 'replacement'} no sooner than 7 days from now, and only if the page has not changed since; withdraw it with withdraw_page_mark.`,
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

export const MARK_TOOL_DEFS = [
  {
    name: 'mark_page_for_deletion',
    description: 'Ask for an existing page to be deleted. The page is not changed now; a reviewed job deletes it no sooner than 7 days later, only if the page has not changed since the mark. Withdraw with withdraw_page_mark.',
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
    description: 'Ask for an existing page to be replaced by new complete content. The page is not changed now; a reviewed job applies it no sooner than 7 days later, only if the page has not changed since. Use edit_page or append_page for ordinary changes.',
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
    description: 'Withdraw every open deletion mark or replacement proposal on a page.',
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

export async function ensureMarkTable(engine: Pick<BrainEngine, 'executeRaw'>): Promise<void> {
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

export function engineStore(engine: Pick<BrainEngine, 'executeRaw'>): GuardStore {
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
      const rows = await engine.executeRaw<{ n: number }>(
        `WITH w AS (UPDATE addon_page_marks SET withdrawn_at = now(), outcome = 'withdrawn'
           WHERE slug = $1 AND source_id = $2 AND withdrawn_at IS NULL AND applied_at IS NULL RETURNING 1)
         SELECT count(*)::int AS n FROM w`, [slug, sourceId]);
      return Number(rows[0]?.n ?? 0);
    },
  };
}

export interface GuardOpts {
  upstream: string;
  defaultSource: string;
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
      const res = await doFetch(opts.upstream, { method: 'POST', headers: passHeaders(req), body: text });
      const raw = await res.text();
      const parsed = res.ok ? parseRpcBody(raw) : null;
      if (!parsed?.result || !Array.isArray(parsed.result.tools)) {
        return new Response(raw, { status: res.status, headers: { 'content-type': res.headers.get('content-type') ?? 'application/json', ...extra } });
      }
      const hidden = new Set<string>(REFUSED_TOOLS);
      parsed.result.tools = [...parsed.result.tools.filter((t: any) => !hidden.has(t?.name)), ...MARK_TOOL_DEFS];
      const sid = res.headers.get('mcp-session-id');
      return rpcJson(parsed, sid ? { 'mcp-session-id': sid } : extra);
    }

    if (msg?.method === 'tools/call') {
      const name = String(msg.params?.name ?? '');
      const args = (msg.params?.arguments && typeof msg.params.arguments === 'object') ? msg.params.arguments : {};
      const principal = principalOf(req.headers.get('authorization'));
      let d: Decision;
      try {
        d = await decide(name, args, store, opts.defaultSource, principal);
      } catch (e) {
        log({ tool: name, slug: args.slug ?? null, decision: 'error', error: e instanceof Error ? e.message : String(e) });
        return rpcJson({ jsonrpc: '2.0', id: msg.id, result: { isError: true, content: [{ type: 'text', text: JSON.stringify({ error: 'guard_unavailable', message: 'The write guard could not check this call. Nothing was written. Try again later.' }) }] } }, extra);
      }
      if (d.action !== 'forward') log({ tool: name, slug: args.slug ?? null, source_id: args.source_id ?? opts.defaultSource, decision: d.action, code: d.action === 'refuse' ? d.code : undefined, principal });
      if (d.action === 'refuse') {
        return rpcJson({ jsonrpc: '2.0', id: msg.id, result: { isError: true, content: [{ type: 'text', text: JSON.stringify({ error: d.code, message: d.message }) }] } }, extra);
      }
      if (d.action === 'answer') {
        return rpcJson({ jsonrpc: '2.0', id: msg.id, result: { content: [{ type: 'text', text: d.text }] } }, extra);
      }
    }
    return forward(req, text);
  };
}
