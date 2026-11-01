import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { PGLiteEngine } from 'gbrain/pglite-engine';
import { listOpenLoops, upsertOpenLoop } from '../src/gbrain-internal.ts';
import { makeHandler } from '../src/http.ts';

let engine: PGLiteEngine;

beforeAll(async () => {
  engine = new PGLiteEngine();
  await engine.connect({});
  await engine.initSchema();
}, 60_000);
afterAll(async () => {
  await engine.disconnect();
});
beforeEach(async () => {
  await engine.executeRaw(`DELETE FROM open_loops`);
  await engine.executeRaw(
    `INSERT INTO sources (id, name, config) VALUES ('s1', 's1', '{"kind":"google"}'::jsonb) ON CONFLICT (id) DO NOTHING`,
  );
}, 30_000);

const TOKEN = 'gbrain-addon';
// engine is assigned in beforeAll, so bind the handler per request, not at module load.
const handle = (req: Request) => makeHandler(engine, { token: TOKEN, defaultSource: 's1' })(req);
const auth = { authorization: `Bearer ${TOKEN}`, 'content-type': 'application/json' };
const post = (path: string, body: unknown, headers: Record<string, string> = auth) =>
  handle(new Request(`http://x${path}`, { method: 'POST', headers, body: typeof body === 'string' ? body : JSON.stringify(body) }));

const item = {
  channel: 'fireflies',
  external_id: 'mtg-1-item-1',
  loop_type: 'commitment_owed_by_me',
  counterparty_slug: 'people/bob',
  summary: 'Send Bob the proposal',
  evidence: [{ page_slug: 'meetings/sync', quote: 'I will send it' }],
};

describe('addon http', () => {
  test('health', async () => {
    const r = await handle(new Request('http://x/health', { headers: auth }));
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ ok: true });
  });

  test('auth is required and accepted', async () => {
    expect((await handle(new Request('http://x/health'))).status).toBe(401);
    expect((await post('/loops/open', item, { 'content-type': 'application/json' })).status).toBe(401);
    expect((await post('/loops/open', item, { authorization: 'Bearer nope' })).status).toBe(401);
    expect((await post('/loops/open', item)).status).toBe(200);
  });

  test('no token configured means no auth', async () => {
    const open = makeHandler(engine, { defaultSource: 's1' });
    expect((await open(new Request('http://x/health'))).status).toBe(200);
  });

  test('open creates a loop and defaults source_id', async () => {
    const r = await post('/loops/open', item);
    expect(r.status).toBe(200);
    const out = (await r.json()) as { created: boolean; loop_id: number };
    expect(out.created).toBe(true);
    const rows = await listOpenLoops(engine, { sourceIds: ['s1'], status: 'open' });
    expect(rows).toHaveLength(1);
    expect(rows[0].id).toBe(out.loop_id);
  });

  test('recheck closes a loop', async () => {
    const created = await upsertOpenLoop(engine, {
      sourceId: 's1',
      dedupKey: 'thread:abc:unanswered_inbound',
      loopType: 'unanswered_inbound',
      counterpartySlug: 'people/bob',
      counterpartyEmail: 'bob@example.com',
      summary: 'Reply owed to bob@example.com',
      evidence: [{ message_id: 'abc' }],
      threadId: 'abc',
      detector: 'deterministic_thread',
      lastActivityAt: '2026-01-01T10:00:00Z',
    });
    const r = await post('/loops/recheck', {
      counterparty_slug: 'people/bob',
      messages: [
        { channel: 'linkedin', message_id: 'li-1', from_me: true, sent_at: '2026-01-02T09:00:00Z', text: 'Sending it over tomorrow' },
      ],
    });
    expect(r.status).toBe(200);
    const out = (await r.json()) as { closed: Array<{ loop_id: number }> };
    expect(out.closed.map((c) => c.loop_id)).toEqual([created.id]);
  });

  test('bad params and invalid JSON are 400', async () => {
    const bad = await post('/loops/open', { ...item, channel: 'email' });
    expect(bad.status).toBe(400);
    expect(((await bad.json()) as { error: string }).error).toMatch(/channel/);
    expect((await post('/loops/open', '{not json')).status).toBe(400);
  });

  test('unknown path 404, wrong method 405, big body 413', async () => {
    expect((await post('/nope', {})).status).toBe(404);
    expect((await handle(new Request('http://x/loops/open', { headers: auth }))).status).toBe(405);
    expect((await post('/loops/open', 'x'.repeat(300 * 1024))).status).toBe(413);
  });
});
