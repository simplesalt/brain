/**
 * Post-pass over gbrain's open `unanswered_inbound` loops: ask the reply-check
 * judge whether each one really needs a personal reply, and drop the ones that
 * do not (shipping notices, receipts, pitches, FYIs). gbrain does not reopen a
 * dropped loop unless the thread gets genuinely newer activity
 * (upsertOpenLoop's ON CONFLICT ... WHERE status='open' OR newer
 * last_activity_at), so a drop sticks until a new message arrives; that newer
 * activity changes last_activity_at, which this pass sees as unjudged.
 *
 * State lives in the add-on's own table `addon_reply_checks`.
 */
import type { BrainEngine } from 'gbrain/engine';
import { closeLoop, type LoopEvidence } from './gbrain-internal.ts';
import type { ReplyCheckInput, ReplyVerdict } from './reply-check.ts';

export interface ReviewDeps {
  judge: (input: ReplyCheckInput) => Promise<ReplyVerdict>;
  sourceId: string;
  /** Drop only when needs_reply is false and confidence >= this. Default 0.7. */
  minConfidence?: number;
  /** Max judge calls per pass. Default 25. */
  maxPerPass?: number;
  /** Retry an `unknown` verdict only after this long. Default 15 min. */
  unknownRetryMs?: number;
  log?: (line: string) => void;
}

export interface ReviewSummary {
  candidates: number;
  judged: number;
  dropped: number;
  kept: number;
  unknown: number;
  skipped: number;
}

const CHANNEL_THREAD = /^(fireflies|linkedin|sms|manual):/;
const ready = new WeakSet<object>();

export async function ensureReplyCheckTable(engine: BrainEngine): Promise<void> {
  if (ready.has(engine)) return;
  await engine.executeRaw(
    `CREATE TABLE IF NOT EXISTS addon_reply_checks (
       loop_id BIGINT PRIMARY KEY,
       last_activity_at TIMESTAMPTZ NOT NULL,
       verdict TEXT NOT NULL,
       reason TEXT NOT NULL,
       checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
     )`,
  );
  ready.add(engine);
}

interface Candidate {
  id: number;
  loop_type: string;
  counterparty_slug: string | null;
  counterparty_email: string | null;
  summary: string;
  evidence: LoopEvidence[] | string;
  thread_id: string | null;
  page_slug: string | null;
  lat: string;
}

/**
 * Build the judge input for one loop.
 * Gmail loops: the email thread page, read with `engine.getPage(slug, { sourceId })`
 * (BrainEngine in "gbrain/engine", src/core/engine.ts) -> `page.compiled_truth` / `page.title`,
 * exactly as gbrain's loops_extract reads it (src/core/google/loops-extract.ts).
 * Our own channel loops (thread_id `linkedin:` etc.), or a missing page: the loop's evidence quotes.
 */
async function inputFor(engine: BrainEngine, sourceId: string, c: Candidate): Promise<ReplyCheckInput | null> {
  const evidence: LoopEvidence[] = typeof c.evidence === 'string' ? JSON.parse(c.evidence) : (c.evidence ?? []);
  const base = {
    counterparty_name: c.counterparty_slug ?? undefined,
    counterparty_email: c.counterparty_email ?? undefined,
  };
  const isChannel = c.thread_id !== null && CHANNEL_THREAD.test(c.thread_id);
  const slug = c.page_slug ?? evidence.find((e) => e.page_slug)?.page_slug ?? null;
  if (!isChannel && slug) {
    const page = await engine.getPage(slug, { sourceId });
    const text = (page?.compiled_truth ?? '').trim();
    if (text) return { channel: 'email', ...base, subject: page?.title ?? c.summary, thread_text: text };
  }
  const quotes = evidence.map((e) => e.quote).filter((q): q is string => !!q);
  if (quotes.length === 0) return null;
  return {
    channel: isChannel ? c.thread_id!.split(':')[0] : 'email',
    ...base,
    subject: c.summary,
    messages: quotes.map((q) => ({ from_me: false, text: q, sent_at: new Date(c.lat).toISOString() })),
  };
}

async function record(engine: BrainEngine, c: Candidate, verdict: string, reason: string) {
  await engine.executeRaw(
    `INSERT INTO addon_reply_checks (loop_id, last_activity_at, verdict, reason, checked_at)
     VALUES ($1, $2::timestamptz, $3, $4, now())
     ON CONFLICT (loop_id) DO UPDATE SET
       last_activity_at = EXCLUDED.last_activity_at, verdict = EXCLUDED.verdict,
       reason = EXCLUDED.reason, checked_at = now()`,
    [c.id, c.lat, verdict, reason],
  );
}

export async function reviewNewLoops(engine: BrainEngine, deps: ReviewDeps): Promise<ReviewSummary> {
  const log = deps.log ?? ((l: string) => console.log(l));
  const minConf = deps.minConfidence ?? 0.7;
  const cap = deps.maxPerPass ?? 25;
  const retryMs = deps.unknownRetryMs ?? 15 * 60_000;
  await ensureReplyCheckTable(engine);

  // Open reply-owed loops not yet judged at their current last_activity_at
  // (an old `unknown` is retried after retryMs). The timestamp travels as text so
  // microsecond precision survives the round trip into the watermark.
  const rows = await engine.executeRaw<Candidate>(
    `SELECT l.id, l.loop_type, l.counterparty_slug, l.counterparty_email, l.summary, l.evidence,
            l.thread_id, l.page_slug, l.last_activity_at::text AS lat
       FROM open_loops l
       LEFT JOIN addon_reply_checks c ON c.loop_id = l.id
      WHERE l.status = 'open' AND l.loop_type = 'unanswered_inbound' AND l.source_id = $1
        AND (c.loop_id IS NULL
             OR c.last_activity_at IS DISTINCT FROM l.last_activity_at
             OR (c.verdict = 'unknown' AND c.checked_at < now() - ($2::bigint * interval '1 millisecond')))
      ORDER BY l.last_activity_at DESC, l.id DESC
      LIMIT $3`,
    [deps.sourceId, retryMs, cap],
  );

  const s: ReviewSummary = { candidates: rows.length, judged: 0, dropped: 0, kept: 0, unknown: 0, skipped: 0 };
  for (const raw of rows) {
    const c = { ...raw, id: Number(raw.id) };
    try {
      const input = await inputFor(engine, deps.sourceId, c);
      if (!input) {
        s.skipped++;
        await record(engine, c, 'skipped', 'no thread text to judge');
        continue;
      }
      const v = await deps.judge(input);
      s.judged++;
      if (v.unknown) {
        s.unknown++;
        await record(engine, c, 'unknown', v.reason);
        continue;
      }
      if (!v.needs_reply && v.confidence >= minConf) {
        const res = await closeLoop(engine, deps.sourceId, c.id, `no reply needed: ${v.reason}`, 'dropped');
        if (res.closed) {
          s.dropped++;
          await record(engine, c, 'dropped', v.reason);
        } else {
          s.kept++;
        }
        continue;
      }
      s.kept++;
      await record(engine, c, v.needs_reply ? 'needs_reply' : 'low_confidence', v.reason);
    } catch (e) {
      // Fail open: leave the loop open and unrecorded, so a later pass retries it.
      s.unknown++;
      console.warn(`reply-check: loop ${c.id} failed, kept open:`, e instanceof Error ? e.message : e);
    }
  }
  log(
    `reply-check pass: candidates=${s.candidates} judged=${s.judged} dropped=${s.dropped} kept=${s.kept} unknown=${s.unknown} skipped=${s.skipped} cap=${cap}`,
  );
  return s;
}
