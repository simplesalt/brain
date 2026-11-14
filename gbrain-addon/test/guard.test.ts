import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { PGLiteEngine } from 'gbrain/pglite-engine';
import {
  decide, engineStore, ensureGuardTables, filterFlagged, flagKey, makeGuardHandler, parseRpcBody,
  type GuardStore, type MarkInput, type StoredPage,
} from '../src/guard.ts';

function memStore(pages: Record<string, StoredPage>) {
  const marks: Array<MarkInput & { id: number; withdrawn: boolean }> = [];
  const store: GuardStore = {
    async page(slug, sourceId) { return pages[`${sourceId}:${slug}`] ?? null; },
    async mark(m) {
      let replaced = 0;
      for (const x of marks) if (x.slug === m.slug && x.sourceId === m.sourceId && !x.withdrawn) { x.withdrawn = true; replaced++; }
      marks.push({ ...m, id: marks.length + 1, withdrawn: false });
      return { id: marks.length, replaced };
    },
    async withdraw(slug, sourceId) {
      let n = 0;
      for (const x of marks) if (x.slug === slug && x.sourceId === sourceId && !x.withdrawn) { x.withdrawn = true; n++; }
      return n;
    },
    async flagged() { return new Set(marks.filter(m => m.kind === 'delete' && !m.withdrawn).map(m => flagKey(m.sourceId, m.slug))); },
  };
  return { store, marks };
}

const pages = () => ({
  'default:people/jane': { bytes: 1000, revision: 'r1' },
  'default:notes/tiny': { bytes: 100, revision: 'r2' },
  'default:notes/old': { bytes: 500, revision: 'r3' },
});

describe('guard decisions: refused', () => {
  const { store } = memStore(pages());
  const d = (name: string, args: Record<string, unknown>) => decide(name, args, store, 'default', 'p');

  test('delete_page, put_pages, begin_page_upload', async () => {
    for (const t of ['delete_page', 'put_pages', 'begin_page_upload']) {
      expect(await d(t, { slug: 'people/jane' })).toMatchObject({ action: 'refuse', code: 'guard_tool_refused' });
    }
  });
  test('put_page on an existing page', async () => {
    expect(await d('put_page', { slug: 'people/jane', content: 'x', expected_revision: 'r1' })).toMatchObject({ action: 'refuse', code: 'guard_overwrite_refused' });
  });
  for (const opt of ['allow_shrink', 'force', 'allow_empty', 'drop_timeline']) {
    test(`put_page ${opt}`, async () => {
      expect(await d('put_page', { slug: 'notes/new', content: 'x', [opt]: true })).toMatchObject({ action: 'refuse', code: 'guard_option_refused' });
    });
  }
  test('marks need a reason, an existing page, and content for a replacement', async () => {
    expect(await d('mark_page_for_deletion', { slug: 'people/jane' })).toMatchObject({ action: 'refuse', code: 'guard_mark_invalid' });
    expect(await d('mark_page_for_deletion', { slug: 'people/nobody', reason: 'dup' })).toMatchObject({ action: 'refuse', code: 'guard_mark_invalid' });
    expect(await d('propose_page_replacement', { slug: 'people/jane', reason: 'r' })).toMatchObject({ action: 'refuse', code: 'guard_mark_invalid' });
  });
});

describe('guard decisions: allowed', () => {
  const { store, marks } = memStore(pages());
  const d = (name: string, args: Record<string, unknown>) => decide(name, args, store, 'default', 'p');

  test('put_page creating a new page, option false, other source', async () => {
    expect(await d('put_page', { slug: 'notes/new', content: '# New' })).toEqual({ action: 'forward' });
    expect(await d('put_page', { slug: 'notes/new', content: '# New', force: false })).toEqual({ action: 'forward' });
    expect(await d('put_page', { slug: 'people/jane', source_id: 'google', content: 'x' })).toEqual({ action: 'forward' });
  });
  test('append_page and other writes pass through', async () => {
    expect(await d('append_page', { slug: 'people/jane', text: 'more', request_id: 'x' })).toEqual({ action: 'forward' });
    expect(await d('add_timeline_entry', { slug: 'people/jane', date: '2026-11-12', summary: 's' })).toEqual({ action: 'forward' });
  });
  test('edit_page of any size is forwarded unchanged (gbrain versions the page before every write)', async () => {
    expect(await d('edit_page', { slug: 'people/jane', expected_revision: 'r1', edits: [{ old_text: 'a', new_text: 'abc' }] })).toEqual({ action: 'forward' });
    expect(await d('edit_page', { slug: 'people/jane', expected_revision: 'r1', edits: [{ old_text: 'a'.repeat(900), new_text: '' }] })).toEqual({ action: 'forward' });
    expect(await d('edit_page', { slug: 'notes/tiny', expected_revision: 'r2', edits: [{ old_text: 'a'.repeat(99), new_text: '' }] })).toEqual({ action: 'forward' });
  });
  test('put_page may overwrite an existing page only under an allowed prefix, and options stay refused there', async () => {
    const s2 = memStore({ ...pages(), 'default:ops/people-scan': { bytes: 900, revision: 'r9' } }).store;
    expect(await decide('put_page', { slug: 'ops/people-scan', content: '{}', expected_revision: 'r9' }, s2, 'default', 'p', ['ops/'])).toEqual({ action: 'forward' });
    expect(await decide('put_page', { slug: 'people/jane', content: 'x', expected_revision: 'r1' }, s2, 'default', 'p', ['ops/'])).toMatchObject({ action: 'refuse', code: 'guard_overwrite_refused' });
    expect(await decide('put_page', { slug: 'ops/people-scan', content: '{}', allow_shrink: true }, s2, 'default', 'p', ['ops/'])).toMatchObject({ action: 'refuse', code: 'guard_option_refused' });
    expect(await decide('put_page', { slug: 'ops/people-scan', content: '{}', expected_revision: 'r9' }, s2, 'default', 'p')).toMatchObject({ action: 'refuse', code: 'guard_overwrite_refused' });
  });
  test('get_versions and revert_version pass through', async () => {
    expect(await d('get_versions', { slug: 'people/jane' })).toEqual({ action: 'forward' });
    expect(await d('revert_version', { slug: 'people/jane', version_id: 3 })).toEqual({ action: 'forward' });
  });
  test('reads strip include_deleted and are filtered by default', async () => {
    expect(await d('search', { query: 'jane' })).toEqual({ action: 'forward', args: { query: 'jane' }, filter: true });
    expect(await d('search', { query: 'jane', include_deleted: true })).toEqual({ action: 'forward', args: { query: 'jane' }, filter: false });
  });
  test('mark hides the page from get_page until withdrawn; include_deleted still reads it', async () => {
    const m = await d('mark_page_for_deletion', { slug: 'notes/old', reason: 'duplicate of notes/new' });
    expect(JSON.parse((m as { text: string }).text)).toMatchObject({ status: 'marked', kind: 'delete' });
    expect(await d('get_page', { slug: 'notes/old' })).toMatchObject({ action: 'refuse', code: 'page_flagged_for_deletion' });
    expect(await d('get_page', { slug: 'notes/old', include_deleted: true })).toEqual({ action: 'forward', args: { slug: 'notes/old' }, filter: false });
    const w = await d('withdraw_page_mark', { slug: 'notes/old' });
    expect(JSON.parse((w as { text: string }).text)).toMatchObject({ status: 'withdrawn', withdrawn: 1 });
    expect(await d('get_page', { slug: 'notes/old' })).toEqual({ action: 'forward', args: { slug: 'notes/old' }, filter: true });
  });
  test('a replacement proposal does not hide the page and supersedes earlier marks', async () => {
    await d('mark_page_for_deletion', { slug: 'people/jane', reason: 'dup' });
    const p = await d('propose_page_replacement', { slug: 'people/jane', reason: 'rewrite', content: '---\ntype: person\n---\n# Jane' });
    expect(JSON.parse((p as { text: string }).text)).toMatchObject({ status: 'marked', kind: 'replace', replaced_earlier_marks: 1 });
    expect(await d('get_page', { slug: 'people/jane' })).toMatchObject({ action: 'forward', filter: true });
    expect(marks.filter(x => !x.withdrawn)).toHaveLength(1);
  });
});

describe('filterFlagged', () => {
  test('drops flagged pages from nested result lists only', () => {
    const flagged = new Set([flagKey('default', 'notes/old')]);
    const out = filterFlagged({ results: [{ slug: 'notes/old', score: 1 }, { slug: 'notes/new', score: 0.5 }, { slug: 'notes/old', source_id: 'google' }], total: 3 }, flagged, 'default');
    expect(out).toEqual({ results: [{ slug: 'notes/new', score: 0.5 }, { slug: 'notes/old', source_id: 'google' }], total: 3 });
  });
});

describe('guard http', () => {
  const calls: Array<{ body: any; auth: string | null }> = [];
  const upstream = (async (_url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body ?? ''));
    calls.push({ body, auth: new Headers(init.headers).get('authorization') });
    if (body.method === 'tools/list') {
      const payload = { jsonrpc: '2.0', id: body.id, result: { tools: [{ name: 'get_page', inputSchema: { type: 'object', properties: { slug: {} } } }, { name: 'put_page' }, { name: 'delete_page' }] } };
      return new Response(`event: message\ndata: ${JSON.stringify(payload)}\n\n`, { headers: { 'content-type': 'text/event-stream', 'mcp-session-id': 's1' } });
    }
    const name = body.params?.name;
    const result = name === 'search' ? { results: [{ slug: 'notes/old' }, { slug: 'notes/new' }] } : { ok: true };
    const content = name === 'list_pages'
      ? [{ type: 'text', text: JSON.stringify([{ slug: 'notes/old', source_id: 'default' }, { slug: 'notes/new', source_id: 'default' }]) }, { type: 'text', text: 'Showing 2 pages.' }]
      : [{ type: 'text', text: JSON.stringify(result) }];
    return new Response(JSON.stringify({ jsonrpc: '2.0', id: body.id, result: { content } }), { headers: { 'content-type': 'application/json' } });
  }) as unknown as typeof fetch;
  const mem = memStore(pages());
  const handle = makeGuardHandler(mem.store, { upstream: 'http://gbrain:3131/mcp', defaultSource: 'default', fetchImpl: upstream, log: () => {} });
  const rpc = (method: string, params: unknown) => handle(new Request('http://g/mcp', {
    method: 'POST', headers: { authorization: 'Bearer t', 'content-type': 'application/json', accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 7, method, params }),
  }));
  const tool = (name: string, args: Record<string, unknown>) => rpc('tools/call', { name, arguments: args });

  beforeEach(() => { calls.length = 0; });

  test('tools/list hides refused tools, adds guard tools and include_deleted', async () => {
    const res = await rpc('tools/list', {});
    expect(res.headers.get('content-type')).toBe('text/event-stream');
    const r = parseRpcBody(await res.text());
    const names = r.result.tools.map((t: { name: string }) => t.name);
    expect(names).toEqual(['get_page', 'put_page', 'mark_page_for_deletion', 'propose_page_replacement', 'withdraw_page_mark']);
    expect(r.result.tools[0].inputSchema.properties.include_deleted.type).toBe('boolean');
  });
  test('a refused call never reaches gbrain and returns a tool error', async () => {
    const r = await (await tool('put_page', { slug: 'people/jane', content: 'x', expected_revision: 'r1' })).json();
    expect(calls).toHaveLength(0);
    expect(r.result.isError).toBe(true);
    expect(JSON.parse(r.result.content[0].text).error).toBe('guard_overwrite_refused');
  });
  test('an allowed call is forwarded with the caller token', async () => {
    const r = await (await tool('append_page', { slug: 'people/jane', text: 'x', request_id: 'q' })).json();
    expect(calls).toHaveLength(1);
    expect(calls[0].auth).toBe('Bearer t');
    expect(r.result.content[0].text).toBe('{"ok":true}');
  });
  test('marking a page for deletion never forwards anything to gbrain', async () => {
    const r = await (await tool('mark_page_for_deletion', { slug: 'notes/old', reason: 'dup' })).json();
    expect(JSON.parse(r.result.content[0].text).status).toBe('marked');
    expect(calls).toHaveLength(0);
    await tool('withdraw_page_mark', { slug: 'notes/old' });
    expect(calls).toHaveLength(0);
  });
  test('a flagged page is hidden from search results and get_page, visible with include_deleted', async () => {
    await tool('mark_page_for_deletion', { slug: 'notes/old', reason: 'dup' });
    calls.length = 0;
    const s = await (await tool('search', { query: 'notes' })).json();
    expect(JSON.parse(s.result.content[0].text).results).toEqual([{ slug: 'notes/new' }]);
    const g = await (await tool('get_page', { slug: 'notes/old' })).json();
    expect(JSON.parse(g.result.content[0].text).error).toBe('page_flagged_for_deletion');
    const lp = await (await tool('list_pages', {})).json();
    expect(JSON.parse(lp.result.content[0].text)).toEqual([{ slug: 'notes/new', source_id: 'default' }]);
    expect(lp.result.content[1].text).toBe('Showing 2 pages.');
    const all = await (await tool('search', { query: 'notes', include_deleted: true })).json();
    expect(JSON.parse(all.result.content[0].text).results).toHaveLength(2);
    expect(calls.every(c => c.body.params.arguments.include_deleted === undefined)).toBe(true);
    await tool('withdraw_page_mark', { slug: 'notes/old' });
  });
  test('a filtered read answers in SSE when gbrain answered in SSE, and in JSON when gbrain did', async () => {
    const sse = (async (_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body ?? ''));
      const payload = { jsonrpc: '2.0', id: body.id, result: { content: [{ type: 'text', text: JSON.stringify({ results: [{ slug: 'notes/old' }, { slug: 'notes/new' }] }) }] } };
      return new Response(`event: message\ndata: ${JSON.stringify(payload)}\n\n`, { headers: { 'content-type': 'text/event-stream' } });
    }) as unknown as typeof fetch;
    const m = memStore(pages());
    const h = makeGuardHandler(m.store, { upstream: 'http://gbrain:3131/mcp', defaultSource: 'default', fetchImpl: sse, log: () => {} });
    const call = (name: string, args: Record<string, unknown>) => h(new Request('http://g/mcp', {
      method: 'POST', headers: { authorization: 'Bearer t', 'content-type': 'application/json', accept: 'application/json, text/event-stream' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 9, method: 'tools/call', params: { name, arguments: args } }),
    }));
    await call('mark_page_for_deletion', { slug: 'notes/old', reason: 'dup' });
    const res = await call('search', { query: 'notes' });
    expect(res.headers.get('content-type')).toBe('text/event-stream');
    const text = await res.text();
    const line = text.split('\n').find(l => l.startsWith('data: '));
    expect(line).toBeDefined();
    const msg = JSON.parse(line!.slice(6));
    expect(msg.id).toBe(9);
    expect(JSON.parse(msg.result.content[0].text).results).toEqual([{ slug: 'notes/new' }]);
    await tool('mark_page_for_deletion', { slug: 'notes/old', reason: 'dup' });
    const j = await tool('search', { query: 'notes' });
    expect(j.headers.get('content-type')).toBe('application/json');
    expect(JSON.parse((await j.json()).result.content[0].text).results).toEqual([{ slug: 'notes/new' }]);
    await tool('withdraw_page_mark', { slug: 'notes/old' });
  });
  test('batched tool calls are refused', async () => {
    const r = await (await handle(new Request('http://g/mcp', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify([{ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'delete_page', arguments: {} } }]) }))).json();
    expect(calls).toHaveLength(0);
    expect(r[0].error.code).toBe(-32600);
  });
});

describe('guard store on a gbrain database', () => {
  let engine: PGLiteEngine;
  beforeAll(async () => {
    engine = new PGLiteEngine();
    await engine.connect({});
    await engine.initSchema();
    await ensureGuardTables(engine);
    await ensureGuardTables(engine);
  }, 60_000);
  afterAll(async () => { await engine.disconnect(); });

  test('page lookup, mark, flag, supersede and withdraw; no archive table', async () => {
    await engine.executeRaw(`INSERT INTO sources (id, name, config) VALUES ('default', 'default', '{}'::jsonb) ON CONFLICT (id) DO NOTHING`);
    await engine.putPage('people/guard-test', { type: 'person', title: 'Guard Test', compiled_truth: 'x'.repeat(500), timeline: '' } as never);
    const store = engineStore(engine, 0);
    const page = await store.page('people/guard-test', 'default');
    expect(page?.bytes).toBeGreaterThanOrEqual(500);
    expect(await store.page('people/none', 'default')).toBeNull();
    await store.mark({ kind: 'delete', slug: 'people/guard-test', sourceId: 'default', reason: 'r', content: null, principal: 'p', page: page! });
    expect((await store.flagged()).has(flagKey('default', 'people/guard-test'))).toBe(true);
    const b = await store.mark({ kind: 'replace', slug: 'people/guard-test', sourceId: 'default', reason: 'r', content: 'new', principal: 'p', page: page! });
    expect(b.replaced).toBe(1);
    expect((await store.flagged()).size).toBe(0);
    expect(await store.withdraw('people/guard-test', 'default', 'p')).toBe(1);
    const t = await engine.executeRaw<{ n: number }>(`SELECT count(*)::int AS n FROM information_schema.tables WHERE table_name = 'addon_page_archives'`);
    expect(Number(t[0].n)).toBe(0);
  });
});
