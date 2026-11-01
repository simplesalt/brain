import type { BrainEngine } from 'gbrain/engine';
import {
  closeLoop,
  detectThreadLoop,
  isAcknowledgementOnly,
  listOpenLoops,
  loadSuppressions,
  upsertOpenLoop,
  type GmailMessageMeta,
  type OpenLoopRow,
  type SuppressionSet,
} from './gbrain-internal.ts';
import * as v from './validate.ts';
import type { ReplyCheckInput, ReplyVerdict } from './reply-check.ts';

export const JOB_OPEN = 'addon-loop-open';
export const JOB_RECHECK = 'addon-loop-recheck';
export const JOB_THREAD = 'addon-loop-thread';

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
  text: string;
}

function parseMessages(J: string, raw: unknown, defaultChannel?: v.Channel): Msg[] {
  if (!Array.isArray(raw)) throw new v.AddonParamError(J, '"messages" must be an array');
  return raw.map((m, i) => {
    const r = v.asRecord(J, m);
    const where = `messages[${i}]`;
    try {
      if (typeof r.from_me !== 'boolean') throw new v.AddonParamError(J, '"from_me" must be a boolean');
      if (typeof r.text !== 'string') throw new v.AddonParamError(J, '"text" must be a string');
      return {
        text: r.text,
        channel: defaultChannel ?? v.channel(J, r.channel, 'channel'),
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
  const all = parseMessages(J, o.messages).sort((a, b) => a.ms - b.ms);
  // My acknowledgement-only message ("Thanks!") is not a reply: it settles nothing.
  const ignored = all
    .filter((m) => m.from_me && isAcknowledgementOnly(m.text))
    .map((m) => ({ channel: m.channel, message_id: m.message_id, reason: 'acknowledgement_only' }));
  const messages = all.filter((m) => !(m.from_me && isAcknowledgementOnly(m.text)));

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
  return { source_id: sourceId, counterparty_slug: slug, closed, left_open, needs_review, ignored };
}

// ── addon-loop-thread ───────────────────────────────────────────────────────

type ThreadType = 'unanswered_inbound' | 'unanswered_outbound';

function slugifyAddr(slug: string): string {
  return slug.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'unknown';
}

/**
 * Assess one non-email conversation with gbrain's own email rule
 * (detectThreadLoop): one loop per conversation and type, only when a reply is
 * owed, grace windows 24h inbound / 72h outbound. The conversation is mapped
 * to a synthetic Gmail thread; addresses are `*.invalid` so gbrain's noise-sender
 * rule cannot match them.
 */
export interface ThreadDeps {
  /** Reply-needed judge for the `unanswered_inbound` gate; absent = no gate. */
  judge?: (input: ReplyCheckInput) => Promise<ReplyVerdict>;
  /** Drop an inbound loop only at/above this confidence. Default 0.7. */
  minConfidence?: number;
}

export async function loopThread(engine: BrainEngine, data: unknown, deps: ThreadDeps = {}) {
  const J = JOB_THREAD;
  const o = v.asRecord(J, data);
  const sourceId = v.sourceId(J, o);
  const channel = v.channel(J, o.channel);
  const threadRaw = v.reqString(J, o, 'thread_id', 200);
  const slug = v.reqString(J, o, 'counterparty_slug', 300);
  const name = v.optString(J, o, 'counterparty_name', 300) ?? slug;
  const now = o.now === undefined || o.now === null ? new Date() : new Date(v.isoDate(J, o.now, 'now').ms);
  const msgs = parseMessages(J, o.messages, channel).sort((a, b) => a.ms - b.ms);

  const me = `me@${channel}.invalid`;
  const them = `${slugifyAddr(slug)}@${channel}.invalid`;
  const threadId = `${channel}:${threadRaw}`.toLowerCase();
  const subject = `${channel} conversation with ${name}`;
  const meta: GmailMessageMeta[] = msgs.map((m) => {
    return {
      id: m.message_id,
      threadId,
      from: m.from_me ? me : them,
      fromAddress: m.from_me ? me : them,
      to: [m.from_me ? them : me],
      cc: [],
      subject,
      dateIso: new Date(m.ms).toISOString(),
      internalDateMs: m.ms,
      labelIds: [],
      listUnsubscribe: false,
      bodyText: m.text,
    };
  });

  // Suppressions are lowercase emails/thread ids: a thread mute on
  // `<channel>:<thread_id>` or a sender mute on the synthetic address matches.
  const suppressions: SuppressionSet = await loadSuppressions(engine, sourceId);
  const verdict = detectThreadLoop({ threadId, account: me, messages: meta }, new Set([me]), now, suppressions);

  // Reply-needed gate: gbrain's rule says a reply is owed; ask whether the owner
  // really needs to respond. "No" opens nothing (fail open on any doubt or error).
  let skipped_by_reply_check: { reason: string; confidence: number } | undefined;
  if (deps.judge && verdict.open.some((s) => s.loopType === 'unanswered_inbound')) {
    const verdictIn = await deps.judge({
      channel,
      counterparty_name: name,
      subject,
      messages: msgs.slice(-6).map((m) => ({ from_me: m.from_me, text: m.text, sent_at: new Date(m.ms).toISOString() })),
    });
    if (!verdictIn.needs_reply && !verdictIn.unknown && verdictIn.confidence >= (deps.minConfidence ?? 0.7)) {
      skipped_by_reply_check = { reason: verdictIn.reason, confidence: verdictIn.confidence };
      verdict.open = verdict.open.filter((s) => s.loopType !== 'unanswered_inbound');
    }
  }

  const opened: Array<{ loop_id: number; dedup_key: string; loop_type: ThreadType; created: boolean }> = [];
  const closed: Array<{ loop_id: number; dedup_key: string; loop_type: ThreadType; settled_by: string }> = [];
  const desired = new Set(verdict.open.map((s) => s.loopType));

  for (const spec of verdict.open) {
    const dedupKey = `${channel}:${threadRaw}:${spec.loopType}`;
    const summary =
      spec.loopType === 'unanswered_inbound'
        ? `Reply owed to ${name} on ${channel}`
        : `Waiting on ${name} on ${channel}`;
    const res = await upsertOpenLoop(engine, {
      sourceId,
      dedupKey,
      loopType: spec.loopType,
      counterpartySlug: slug,
      summary,
      evidence: spec.evidence,
      threadId: `${channel}:${threadRaw}`,
      detector: 'manual',
      lastActivityAt: new Date(spec.lastActivityMs).toISOString(),
      openedAt: new Date(spec.openedMs).toISOString(),
    });
    opened.push({ loop_id: res.id, dedup_key: dedupKey, loop_type: spec.loopType, created: res.created });
  }

  const lastId = msgs.length > 0 ? msgs[msgs.length - 1].message_id : '';
  for (const type of verdict.close.filter((t) => !desired.has(t))) {
    const dedupKey = `${channel}:${threadRaw}:${type}`;
    const rows = (await listOpenLoops(engine, { sourceIds: [sourceId], status: 'open', loopType: type, limit: 1000 })) as OpenLoopRow[];
    for (const loop of rows.filter((r) => r.dedup_key === dedupKey)) {
      const res = await closeLoop(engine, sourceId, loop.id, settleNote(channel, lastId));
      if (res.closed) closed.push({ loop_id: loop.id, dedup_key: dedupKey, loop_type: type, settled_by: `${channel}:${lastId}` });
    }
  }

  const held_until = verdict.held ? new Date(verdict.held.untilMs).toISOString() : undefined;
  const verdictName = skipped_by_reply_check
    ? 'skipped_by_reply_check'
    : verdict.open.length
    ? verdict.open[0].loopType === 'unanswered_inbound'
      ? 'reply_owed'
      : 'waiting_on_them'
    : held_until
      ? 'held'
      : closed.length
        ? 'settled'
        : 'nothing_owed';
  return {
    source_id: sourceId,
    thread_id: threadId,
    opened,
    closed,
    ...(held_until ? { held_until } : {}),
    ...(skipped_by_reply_check ? { skipped_by_reply_check: skipped_by_reply_check.reason, reply_check_confidence: skipped_by_reply_check.confidence } : {}),
    verdict: verdictName,
  };
}
