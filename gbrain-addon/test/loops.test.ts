import { afterAll, beforeAll, beforeEach, describe, expect, test } from 'bun:test';
import { PGLiteEngine } from 'gbrain/pglite-engine';
import { listOpenLoops, upsertOpenLoop } from '../src/gbrain-internal.ts';
import { loopOpen, loopRecheck } from '../src/jobs.ts';

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

const actionItem = {
  source_id: 's1',
  channel: 'fireflies',
  external_id: 'mtg-42-item-1',
  loop_type: 'commitment_owed_by_me',
  counterparty_slug: 'people/bob',
  summary: 'Send Bob the revised proposal',
  due_at: '2026-03-01T00:00:00Z',
  evidence: [{ page_slug: 'meetings/2026-02-20-sync', quote: 'I will send the proposal by Friday' }],
};

describe('addon-loop-open', () => {
  test('a meeting action item opens a manual loop', async () => {
    const r = await loopOpen(engine, actionItem);
    expect(r.created).toBe(true);
    expect(r.dedup_key).toBe('fireflies:mtg-42-item-1:commitment_owed_by_me');
    const [row] = await listOpenLoops(engine, { sourceIds: ['s1'], status: 'open' });
    expect(row.id).toBe(r.loop_id);
    expect(row.detector).toBe('manual');
    expect(row.thread_id).toBe('fireflies:mtg-42-item-1');
    expect(row.counterparty_slug).toBe('people/bob');
    expect(row.evidence[0].page_slug).toBe('meetings/2026-02-20-sync');
  });

  test('re-submitting is idempotent', async () => {
    const a = await loopOpen(engine, actionItem);
    const b = await loopOpen(engine, actionItem);
    expect(b.loop_id).toBe(a.loop_id);
    expect(b.created).toBe(false);
    expect(await listOpenLoops(engine, { sourceIds: ['s1'] })).toHaveLength(1);
  });

  test('bad params throw clear errors', async () => {
    await expect(loopOpen(engine, { ...actionItem, channel: 'email' })).rejects.toThrow(/channel/);
    await expect(loopOpen(engine, { ...actionItem, loop_type: 'nope' })).rejects.toThrow(/loop_type/);
    await expect(loopOpen(engine, { ...actionItem, summary: '' })).rejects.toThrow(/summary/);
    await expect(loopOpen(engine, { ...actionItem, due_at: 'soon' })).rejects.toThrow(/due_at/);
    await expect(loopOpen(engine, { ...actionItem, evidence: 'x' })).rejects.toThrow(/evidence/);
    await expect(loopOpen(engine, null)).rejects.toThrow(/object/);
  });
});

describe('addon-loop-recheck', () => {
  async function emailLoop(type: 'unanswered_inbound' | 'commitment_owed_by_me') {
    const r = await upsertOpenLoop(engine, {
      sourceId: 's1',
      dedupKey: `thread:abc:${type}`,
      loopType: type,
      counterpartySlug: 'people/bob',
      counterpartyEmail: 'bob@example.com',
      summary: 'Reply owed to bob@example.com',
      evidence: [{ message_id: 'abc' }],
      threadId: 'abc',
      detector: 'deterministic_thread',
      lastActivityAt: '2026-01-01T10:00:00Z',
    });
    return r.id;
  }
  const reply = {
    channel: 'linkedin',
    message_id: 'li-msg-9',
    from_me: true,
    sent_at: '2026-01-02T09:00:00Z',
    text: 'Thanks Bob, will do',
  };

  test('a LinkedIn reply from me closes an email unanswered_inbound loop, noting the message', async () => {
    const id = await emailLoop('unanswered_inbound');
    const r = await loopRecheck(engine, { source_id: 's1', counterparty_slug: 'people/bob', messages: [reply] });
    expect(r.closed.map((c) => c.loop_id)).toEqual([id]);
    expect(r.closed[0].settled_by).toBe('linkedin:li-msg-9');
    const [row] = await engine.executeRaw<{ status: string; closed_by: string }>(
      `SELECT status, closed_by FROM open_loops WHERE id = $1`,
      [id],
    );
    expect(row.status).toBe('done');
    expect(row.closed_by).toContain('linkedin:li-msg-9');
  });

  test('an earlier message or an inbound message does not settle it', async () => {
    const id = await emailLoop('unanswered_inbound');
    const r = await loopRecheck(engine, {
      source_id: 's1',
      counterparty_slug: 'people/bob',
      messages: [
        { ...reply, sent_at: '2025-12-31T09:00:00Z' },
        { ...reply, message_id: 'li-2', from_me: false },
      ],
    });
    expect(r.closed).toHaveLength(0);
    expect(r.left_open.map((l) => l.loop_id)).toEqual([id]);
  });

  test('a commitment loop is left open and flagged for review', async () => {
    const id = await emailLoop('commitment_owed_by_me');
    const r = await loopRecheck(engine, { source_id: 's1', counterparty_slug: 'people/bob', messages: [reply] });
    expect(r.closed).toHaveLength(0);
    expect(r.needs_review.map((n) => n.loop_id)).toEqual([id]);
    expect(await listOpenLoops(engine, { sourceIds: ['s1'], status: 'open' })).toHaveLength(1);
  });

  test('bad params throw', async () => {
    await expect(loopRecheck(engine, { source_id: 's1', counterparty_slug: 'p', messages: 'x' })).rejects.toThrow(/messages/);
    await expect(
      loopRecheck(engine, { source_id: 's1', counterparty_slug: 'p', messages: [{ ...reply, sent_at: 'bad' }] }),
    ).rejects.toThrow(/sent_at/);
    await expect(
      loopRecheck(engine, { source_id: 's1', counterparty_slug: 'p', messages: [{ ...reply, from_me: 'yes' }] }),
    ).rejects.toThrow(/from_me/);
    await expect(loopRecheck(engine, { source_id: 's1', messages: [] })).rejects.toThrow(/counterparty_slug/);
  });
});
