import type { BrainEngine } from 'gbrain/engine';
import { closeLoop, listOpenLoops, upsertOpenLoop, type OpenLoopRow } from './gbrain-internal.ts';
import * as v from './validate.ts';

export const JOB_OPEN = 'addon-loop-open';
export const JOB_RECHECK = 'addon-loop-recheck';

// ── addon-loop-open ─────────────────────────────────────────────────────────

export async function loopOpen(engine: BrainEngine, data: unknown) {
  const J = JOB_OPEN;
  const o = v.asRecord(J, data);
  const sourceId = v.sourceId(J, o);
  const channel = v.channel(J, o.channel);
  const externalId = v.reqString(J, o, 'external_id', 200);
  const loopType = v.loopType(J, o.loop_type);
  const summary = v.reqString(J, o, 'summary', 1000);
  const threadRaw = v.optString(J, o, 'thread_id', 200);
  const slug = v.optString(J, o, 'counterparty_slug', 300);
  const email = v.optString(J, o, 'counterparty_email', 320)?.toLowerCase();
  const due = o.due_at === undefined || o.due_at === null ? undefined : v.isoDate(J, o.due_at, 'due_at');
  const evidence = o.evidence === undefined ? [] : v.evidence(J, o.evidence);

  const dedupKey = `${channel}:${externalId}:${loopType}`;
  const res = await upsertOpenLoop(engine, {
    sourceId,
    dedupKey,
    loopType,
    counterpartySlug: slug ?? null,
    counterpartyEmail: email ?? null,
    summary,
    evidence,
    threadId: `${channel}:${threadRaw ?? externalId}`,
    pageSlug: evidence.find((e) => e.page_slug)?.page_slug ?? null,
    dueAt: due?.iso ?? null,
    detector: 'manual',
  });
  return { loop_id: res.id, dedup_key: dedupKey, created: res.created, applied: res.applied };
}

// ── addon-loop-recheck ──────────────────────────────────────────────────────

interface Msg {
  channel: v.Channel;
  message_id: string;
  page_slug?: string;
  from_me: boolean;
  ms: number;
}

function parseMessages(J: string, raw: unknown): Msg[] {
  if (!Array.isArray(raw)) throw new v.AddonParamError(J, '"messages" must be an array');
  return raw.map((m, i) => {
    const r = v.asRecord(J, m);
    const where = `messages[${i}]`;
    try {
      if (typeof r.from_me !== 'boolean') throw new v.AddonParamError(J, '"from_me" must be a boolean');
      v.reqString(J, r, 'text', 1_000_000);
      return {
        channel: v.channel(J, r.channel, 'channel'),
        message_id: v.reqString(J, r, 'message_id', 200),
        page_slug: v.optString(J, r, 'page_slug', 300),
        from_me: r.from_me,
        ms: v.isoDate(J, r.sent_at, 'sent_at').ms,
      };
    } catch (e) {
      if (e instanceof v.AddonParamError) throw new v.AddonParamError(J, `${where}: ${e.message.slice(J.length + 2)}`);
      throw e;
    }
  });
}

/** Close note: recites the settling message as `${channel}:${message_id}`, capped at 200. */
export function settleNote(channel: string, messageId: string): string {
  const ref = `${channel}:${messageId}`;
  return `settled by ${ref}`.slice(0, 200);
}

export async function loopRecheck(engine: BrainEngine, data: unknown) {
  const J = JOB_RECHECK;
  const o = v.asRecord(J, data);
  const sourceId = v.sourceId(J, o);
  const slug = v.reqString(J, o, 'counterparty_slug', 300);
  const messages = parseMessages(J, o.messages).sort((a, b) => a.ms - b.ms);

  const loops = await listOpenLoops(engine, { sourceIds: [sourceId], status: 'open', counterparty: slug, limit: 1000 });
  const closed: Array<{ loop_id: number; reason: string; settled_by: string; fact_expired?: boolean; retryable?: boolean }> = [];
  const left_open: Array<{ loop_id: number; reason: string }> = [];
  const needs_review: Array<{ loop_id: number; loop_type: string; reason: string }> = [];

  for (const loop of loops as OpenLoopRow[]) {
    if (loop.loop_type !== 'unanswered_inbound' && loop.loop_type !== 'unanswered_outbound') {
      const reason = 'needs_review: only unanswered_* loops are settled automatically';
      left_open.push({ loop_id: loop.id, reason });
      needs_review.push({ loop_id: loop.id, loop_type: loop.loop_type, reason });
      continue;
    }
    // inbound loop = I owe a reply -> settled by a later message from me;
    // outbound loop = they owe a reply -> settled by a later message from them.
    const wantFromMe = loop.loop_type === 'unanswered_inbound';
    const since = Date.parse(loop.last_activity_at);
    const settler = messages.find((m) => m.from_me === wantFromMe && m.ms > since);
    if (!settler) {
      left_open.push({ loop_id: loop.id, reason: 'no later settling message' });
      continue;
    }
    const ref = `${settler.channel}:${settler.message_id}`;
    const res = await closeLoop(engine, sourceId, loop.id, settleNote(settler.channel, settler.message_id));
    if (res.closed) {
      closed.push({
        loop_id: loop.id,
        reason: wantFromMe ? 'I replied' : 'they replied',
        settled_by: ref,
        fact_expired: res.fact_expired,
        retryable: res.retryable,
      });
    } else {
      left_open.push({ loop_id: loop.id, reason: `close refused: ${res.reason ?? 'unknown'}` });
    }
  }
  return { source_id: sourceId, counterparty_slug: slug, closed, left_open, needs_review };
}
