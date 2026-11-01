import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { PGLiteEngine } from 'gbrain/pglite-engine';
import { listOpenLoops, upsertOpenLoop } from '../src/gbrain-internal.ts';
import { makeHandler } from '../src/http.ts';
import { loopRecheck, loopThread } from '../src/jobs.ts';

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

const NOW = Date.parse('2026-03-10T12:00:00Z');
const ago = (hours: number) => new Date(NOW - hours * 3_600_000).toISOString();
const msg = (id: string, fromMe: boolean, hoursAgo: number, text: string) => ({
  message_id: id,
  from_me: fromMe,
  sent_at: ago(hoursAgo),
  text,
});
const conv = (messages: unknown[], extra: Record<string, unknown> = {}) => ({
  source_id: 's1',
  channel: 'linkedin',
  thread_id: 'conv-1',
  counterparty_slug: 'people/bob',
  counterparty_name: 'Bob',
  now: new Date(NOW).toISOString(),
  messages,
  ...extra,
});
const open = () => listOpenLoops(engine, { sourceIds: ['s1'], status: 'open' });

describe('addon-loop-thread', () => {
  test('a fresh inbound message is held and opens nothing', async () => {
    const r = await loopThread(engine, conv([msg('m1', false, 2, 'Can you send the deck?')]));
    expect(r.opened).toHaveLength(0);
    expect(r.verdict).toBe('held');
    expect(r.held_until).toBe(ago(-22));
    expect(await open()).toHaveLength(0);
  });

  test('the same inbound message 25h later opens one loop', async () => {
    const r = await loopThread(engine, conv([msg('m1', false, 2, 'Can you send the deck?')], { now: ago(-23) }));
    expect(r.opened).toHaveLength(1);
    expect(r.verdict).toBe('reply_owed');
    const [row] = await open();
    expect(row.loop_type).toBe('unanswered_inbound');
    expect(row.detector).toBe('manual');
    expect(row.dedup_key).toBe('linkedin:conv-1:unanswered_inbound');
    expect(row.thread_id).toBe('linkedin:conv-1');
    expect(row.counterparty_slug).toBe('people/bob');
    expect(row.summary).toBe('Reply owed to Bob on linkedin');
    expect(row.evidence[0].message_id).toBe('m1');
    expect(row.evidence[0].quote).toBe('Can you send the deck?');
    expect(Date.parse(row.opened_at)).toBe(NOW - 2 * 3_600_000);
  });

  test('two inbound messages still make one loop, opened at the first', async () => {
    const r = await loopThread(engine, conv([msg('m2', false, 26, 'Any news?'), msg('m1', false, 30, 'Can you send the deck?')]));
    expect(r.opened).toHaveLength(1);
    const rows = await open();
    expect(rows).toHaveLength(1);
    expect(Date.parse(rows[0].opened_at)).toBe(NOW - 30 * 3_600_000);
    expect(rows[0].evidence[0].message_id).toBe('m2');
  });

  test('my substantive reply later closes the loop', async () => {
    const inbound = msg('m1', false, 30, 'Can you send the deck?');
    await loopThread(engine, conv([inbound]));
    expect(await open()).toHaveLength(1);
    const r = await loopThread(engine, conv([inbound, msg('m2', true, 20, 'Yes, attached the deck below.')]));
    expect(r.closed).toHaveLength(1);
    expect(r.closed[0].settled_by).toBe('linkedin:m2');
    expect(r.verdict).toBe('settled');
    expect(await open()).toHaveLength(0);
    const [row] = await engine.executeRaw<{ status: string; closed_by: string }>(`SELECT status, closed_by FROM open_loops`);
    expect(row.status).toBe('done');
    expect(row.closed_by).toContain('linkedin:m2');
  });

  test('my "Thanks!" to their question leaves the loop open', async () => {
    const r = await loopThread(
      engine,
      conv([msg('m1', false, 30, 'Can you send the deck?'), msg('m2', true, 28, 'Thanks!')]),
    );
    expect(r.opened).toHaveLength(1);
    expect(r.closed).toHaveLength(0);
    expect((await open())[0].loop_type).toBe('unanswered_inbound');
  });

  test('my outbound statement without a question opens nothing', async () => {
    const r = await loopThread(engine, conv([msg('m1', true, 100, 'Sent you the invoice.')]));
    expect(r.opened).toHaveLength(0);
    expect(r.verdict).toBe('nothing_owed');
    expect(await open()).toHaveLength(0);
  });

  test('my outbound question 73h old opens one unanswered_outbound loop', async () => {
    const r = await loopThread(engine, conv([msg('m1', true, 73, 'Are you free to talk this week?')]));
    expect(r.opened).toHaveLength(1);
    expect(r.verdict).toBe('waiting_on_them');
    const rows = await open();
    expect(rows).toHaveLength(1);
    expect(rows[0].loop_type).toBe('unanswered_outbound');
    expect(rows[0].summary).toBe('Waiting on Bob on linkedin');
    // Younger than 72h is held, not opened.
    const held = await loopThread(engine, conv([msg('m1', true, 10, 'Are you free to talk this week?')], { thread_id: 'conv-2' }));
    expect(held.opened).toHaveLength(0);
    expect(held.held_until).toBe(ago(-62));
  });

  test('re-posting the same conversation is idempotent', async () => {
    const body = conv([msg('m1', false, 30, 'Can you send the deck?')]);
    const a = await loopThread(engine, body);
    const b = await loopThread(engine, body);
    expect(b.opened[0].loop_id).toBe(a.opened[0].loop_id);
    expect(b.opened[0].created).toBe(false);
    expect(await open()).toHaveLength(1);
  });

  test('bad params throw', async () => {
    await expect(loopThread(engine, conv([], { channel: 'email' }))).rejects.toThrow(/channel/);
    await expect(loopThread(engine, conv([], { thread_id: '' }))).rejects.toThrow(/thread_id/);
    await expect(loopThread(engine, conv('x'))).rejects.toThrow(/messages/);
    await expect(loopThread(engine, conv([{ ...msg('m', true, 1, 'x'), sent_at: 'bad' }]))).rejects.toThrow(/sent_at/);
  });
});

describe('POST /loops/thread', () => {
  const TOKEN = 't';
  const handle = (req: Request) => makeHandler(engine, { token: TOKEN, defaultSource: 's1' })(req);
  const call = (body: unknown, token = TOKEN) =>
    handle(
      new Request('http://x/loops/thread', {
        method: 'POST',
        headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
        body: JSON.stringify(body),
      }),
    );

  test('requires auth and defaults source_id', async () => {
    expect((await call(conv([]), 'wrong')).status).toBe(401);
    const { source_id: _omit, ...noSource } = conv([msg('m1', false, 30, 'Can you send the deck?')]);
    const r = await call(noSource);
    expect(r.status).toBe(200);
    const out = (await r.json()) as { opened: unknown[]; verdict: string };
    expect(out.opened).toHaveLength(1);
    expect(out.verdict).toBe('reply_owed');
  });

  test('bad params are 400', async () => {
    expect((await call(conv([], { channel: 'email' }))).status).toBe(400);
  });
});

describe('addon-loop-recheck ignores acknowledgements', () => {
  test('my "Thanks!" does not settle a reply-owed loop and is reported ignored', async () => {
    const created = await upsertOpenLoop(engine, {
      sourceId: 's1',
      dedupKey: 'thread:abc:unanswered_inbound',
      loopType: 'unanswered_inbound',
      counterpartySlug: 'people/bob',
      summary: 'Reply owed',
      evidence: [{ message_id: 'abc' }],
      detector: 'deterministic_thread',
      lastActivityAt: '2026-01-01T10:00:00Z',
    });
    const r = await loopRecheck(engine, {
      source_id: 's1',
      counterparty_slug: 'people/bob',
      messages: [{ channel: 'linkedin', message_id: 'li-1', from_me: true, sent_at: '2026-01-02T09:00:00Z', text: 'Thanks!' }],
    });
    expect(r.closed).toHaveLength(0);
    expect(r.left_open.map((l) => l.loop_id)).toEqual([created.id]);
    expect(r.ignored).toEqual([{ channel: 'linkedin', message_id: 'li-1', reason: 'acknowledgement_only' }]);
  });
});
