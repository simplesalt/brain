import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { PGLiteEngine } from 'gbrain/pglite-engine';
import { decide, engineStore, ensureMarkTable, makeGuardHandler, type GuardStore, type MarkInput, type StoredPage } from '../src/guard.ts';

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
  };
  return { store, marks };
}

const pages = {
  'default:people/jane': { bytes: 1000, revision: 'r1' },
  'default:notes/tiny': { bytes: 100, revision: 'r2' },
};

describe('guard decisions: refused', () => {
  const { store } = memStore(pages);
  const d = (name: string, args: Record<string, unknown>) => decide(name, args, store, 'default', 'p');

  test('delete_page', async () => {
    expect(await d('delete_page', { slug: 'people/jane' })).toMatchObject({ action: 'refuse', code: 'guard_tool_refused' });
  });
  test('put_pages and begin_page_upload', async () => {
    expect(await d('put_pages', { pages: [] })).toMatchObject({ action: 'refuse', code: 'guard_tool_refused' });
    expect(await d('begin_page_upload', { slug: 'x' })).toMatchObject({ action: 'refuse', code: 'guard_tool_refused' });
  });
  test('put_page on an existing page', async () => {
    expect(await d('put_page', { slug: 'people/jane', content: 'x', expected_revision: 'r1' })).toMatchObject({ action: 'refuse', code: 'guard_overwrite_refused' });
  });
  for (const opt of ['allow_shrink', 'force', 'allow_empty', 'drop_timeline']) {
    test(`put_page ${opt}`, async () => {
      expect(await d('put_page', { slug: 'notes/new', content: 'x', [opt]: true })).toMatchObject({ action: 'refuse', code: 'guard_option_refused' });
    });
  }
  test('edit_page removing more than 80%', async () => {
    const r = await d('edit_page', { slug: 'people/jane', expected_revision: 'r1', edits: [{ old_text: 'a'.repeat(900), new_text: 'b' }] });
    expect(r).toMatchObject({ action: 'refuse', code: 'guard_gutting_refused' });
  });
  test('edit_page leaving a page nearly empty', async () => {
    const r = await d('edit_page', { slug: 'notes/tiny', expected_revision: 'r2', edits: [{ old_text: 'a'.repeat(50), new_text: '' }] });
    expect(r).toMatchObject({ action: 'refuse', code: 'guard_gutting_refused' });
  });
  test('edit_page removals summed across edits', async () => {
    const r = await d('edit_page', { slug: 'people/jane', expected_revision: 'r1', edits: [{ old_text: 'a'.repeat(450), new_text: '' }, { old_text: 'c'.repeat(400), new_text: '' }] });
    expect(r).toMatchObject({ action: 'refuse', code: 'guard_gutting_refused' });
  });
  test('mark without a reason, and on a missing page', async () => {
    expect(await d('mark_page_for_deletion', { slug: 'people/jane' })).toMatchObject({ action: 'refuse', code: 'guard_mark_invalid' });
    expect(await d('mark_page_for_deletion', { slug: 'people/nobody', reason: 'dup' })).toMatchObject({ action: 'refuse', code: 'guard_mark_invalid' });
    expect(await d('propose_page_replacement', { slug: 'people/jane', reason: 'r' })).toMatchObject({ action: 'refuse', code: 'guard_mark_invalid' });
  });
});

describe('guard decisions: allowed', () => {
  const { store, marks } = memStore(pages);
  const d = (name: string, args: Record<string, unknown>) => decide(name, args, store, 'default', 'p');

  test('put_page creating a new page', async () => {
    expect(await d('put_page', { slug: 'notes/new', content: '# New' })).toEqual({ action: 'forward' });
  });
  test('put_page with option false is fine', async () => {
    expect(await d('put_page', { slug: 'notes/new', content: '# New', force: false })).toEqual({ action: 'forward' });
  });
  test('put_page in another source where the slug is free', async () => {
    expect(await d('put_page', { slug: 'people/jane', source_id: 'google', content: 'x' })).toEqual({ action: 'forward' });
  });
  test('append_page', async () => {
    expect(await d('append_page', { slug: 'people/jane', text: 'more', request_id: 'x' })).toEqual({ action: 'forward' });
  });
  test('edit_page that grows or swaps', async () => {
    expect(await d('edit_page', { slug: 'people/jane', expected_revision: 'r1', edits: [{ old_text: 'a', new_text: 'abc' }] })).toEqual({ action: 'forward' });
  });
  test('edit_page shrinking under the line', async () => {
    expect(await d('edit_page', { slug: 'people/jane', expected_revision: 'r1', edits: [{ old_text: 'a'.repeat(700), new_text: '' }] })).toEqual({ action: 'forward' });
  });
  test('reads and other tools pass through', async () => {
    expect(await d('get_page', { slug: 'people/jane' })).toEqual({ action: 'forward' });
    expect(await d('add_timeline_entry', { slug: 'people/jane', date: '2026-11-12', summary: 's' })).toEqual({ action: 'forward' });
  });
  test('mark, propose and withdraw record without forwarding', async () => {
    const m = await d('mark_page_for_deletion', { slug: 'people/jane', reason: 'duplicate of people/jane-doe' });
    expect(m.action).toBe('answer');
    const p = await d('propose_page_replacement', { slug: 'people/jane', reason: 'rewrite', content: '---\ntype: person\n---\n# Jane' });
    expect(p.action).toBe('answer');
    expect(JSON.parse((p as { text: string }).text)).toMatchObject({ status: 'marked', kind: 'replace', replaced_earlier_marks: 1 });
    const w = await d('withdraw_page_mark', { slug: 'people/jane' });
    expect(JSON.parse((w as { text: string }).text)).toMatchObject({ status: 'withdrawn', withdrawn: 1 });
    expect(marks.filter(x => !x.withdrawn)).toHaveLength(0);
  });
});

describe('guard http', () => {
  const calls: Array<{ body: string; auth: string | null }> = [];
  const upstream = (async (_url: string, init: RequestInit) => {
    const body = String(init.body ?? '');
    calls.push({ body, auth: new Headers(init.headers).get('authorization') });
    const msg = JSON.parse(body);
    if (msg.method === 'tools/list') {
      const payload = { jsonrpc: '2.0', id: msg.id, result: { tools: [{ name: 'get_page' }, { name: 'put_page' }, { name: 'delete_page' }] } };
      return new Response(`event: message\ndata: ${JSON.stringify(payload)}\n\n`, { headers: { 'content-type': 'text/event-stream', 'mcp-session-id': 's1' } });
    }
    return new Response(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { content: [{ type: 'text', text: '{"ok":true}' }] } }), { headers: { 'content-type': 'application/json' } });
  }) as unknown as typeof fetch;
  const { store } = memStore(pages);
  const handle = makeGuardHandler(store, { upstream: 'http://gbrain:3131/mcp', defaultSource: 'default', fetchImpl: upstream, log: () => {} });
  const rpc = (method: string, params: unknown) => handle(new Request('http://g/mcp', {
    method: 'POST', headers: { authorization: 'Bearer t', 'content-type': 'application/json', accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 7, method, params }),
  }));

  beforeEach(() => { calls.length = 0; });

  test('tools/list hides refused tools and adds the mark tools', async () => {
    const r = await (await rpc('tools/list', {})).json();
    const names = r.result.tools.map((t: { name: string }) => t.name);
    expect(names).toEqual(['get_page', 'put_page', 'mark_page_for_deletion', 'propose_page_replacement', 'withdraw_page_mark']);
  });
  test('a refused call never reaches gbrain and returns a tool error', async () => {
    const r = await (await rpc('tools/call', { name: 'put_page', arguments: { slug: 'people/jane', content: 'x', expected_revision: 'r1' } })).json();
    expect(calls).toHaveLength(0);
    expect(r.result.isError).toBe(true);
    expect(JSON.parse(r.result.content[0].text).error).toBe('guard_overwrite_refused');
  });
  test('an allowed call is forwarded with the caller token', async () => {
    const r = await (await rpc('tools/call', { name: 'append_page', arguments: { slug: 'people/jane', text: 'x', request_id: 'q' } })).json();
    expect(calls).toHaveLength(1);
    expect(calls[0].auth).toBe('Bearer t');
    expect(r.result.content[0].text).toBe('{"ok":true}');
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
    await ensureMarkTable(engine);
  }, 60_000);
  afterAll(async () => { await engine.disconnect(); });

  test('page lookup, mark, supersede and withdraw', async () => {
    await engine.executeRaw(`INSERT INTO sources (id, name, config) VALUES ('default', 'default', '{}'::jsonb) ON CONFLICT (id) DO NOTHING`);
    await engine.putPage('people/guard-test', { type: 'person', title: 'Guard Test', compiled_truth: 'x'.repeat(500), timeline: '' } as never);
    const store = engineStore(engine);
    const page = await store.page('people/guard-test', 'default');
    expect(page?.bytes).toBeGreaterThanOrEqual(500);
    expect(await store.page('people/none', 'default')).toBeNull();
    const a = await store.mark({ kind: 'delete', slug: 'people/guard-test', sourceId: 'default', reason: 'r', content: null, principal: 'p', page: page! });
    const b = await store.mark({ kind: 'replace', slug: 'people/guard-test', sourceId: 'default', reason: 'r', content: 'new', principal: 'p', page: page! });
    expect(b.replaced).toBe(1);
    expect(b.id).toBeGreaterThan(a.id);
    expect(await store.withdraw('people/guard-test', 'default', 'p')).toBe(1);
    const open = await engine.executeRaw<{ n: number }>(`SELECT count(*)::int AS n FROM addon_page_marks WHERE withdrawn_at IS NULL`);
    expect(Number(open[0].n)).toBe(0);
  });
});
