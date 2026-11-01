/**
 * The ONLY file that touches gbrain internals.
 *
 * loops-store.ts is not in gbrain's package.json "exports", so it is resolved
 * by path from the installed package (anchored on the public "gbrain/engine"
 * export, which lives in src/core/). After a gbrain upgrade, this is the one
 * file to re-check (see README "Upgrading gbrain").
 *
 * Everything else in the add-on imports from here, or from gbrain's public
 * exports (gbrain/minions, gbrain/engine, gbrain/operations, ...).
 */
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { BrainEngine } from 'gbrain/engine';
import { operationsByName } from 'gbrain/operations';

// ── Mirrors of src/core/loops/loops-store.ts types (type-only, no runtime dep)
export type LoopType =
  | 'commitment_owed_by_me'
  | 'commitment_owed_to_me'
  | 'unanswered_inbound'
  | 'unanswered_outbound'
  | 'decision_pending';

export const LOOP_TYPES: readonly LoopType[] = [
  'commitment_owed_by_me',
  'commitment_owed_to_me',
  'unanswered_inbound',
  'unanswered_outbound',
  'decision_pending',
];

export interface LoopEvidence {
  message_id?: string;
  page_slug?: string;
  quote?: string;
}

export interface OpenLoopUpsert {
  sourceId: string;
  dedupKey: string;
  loopType: LoopType;
  counterpartySlug?: string | null;
  counterpartyEmail?: string | null;
  summary: string;
  evidence: LoopEvidence[];
  threadId?: string | null;
  pageSlug?: string | null;
  dueAt?: string | null;
  detector: 'deterministic_thread' | 'llm_extract' | 'manual';
  confidence?: number;
  factId?: number | null;
  lastActivityAt?: string | null;
  openedAt?: string | null;
}

export interface OpenLoopRow {
  id: number;
  source_id: string;
  dedup_key: string;
  loop_type: LoopType;
  counterparty_slug: string | null;
  counterparty_email: string | null;
  summary: string;
  evidence: LoopEvidence[];
  thread_id: string | null;
  page_slug: string | null;
  due_at: string | null;
  status: 'open' | 'done' | 'dropped' | 'stale';
  detector: string;
  fact_id: number | null;
  opened_at: string;
  last_activity_at: string;
  closed_at: string | null;
  closed_by: string | null;
}

// ── Mirrors of src/core/loops/loops-store.ts + src/core/google/{types,loop-detect}.ts
export interface SuppressionSet {
  senders: Set<string>;
  threads: Set<string>;
}

export interface GmailMessageMeta {
  id: string;
  threadId: string;
  from: string;
  fromAddress: string;
  to: string[];
  cc: string[];
  subject: string;
  dateIso: string;
  internalDateMs: number;
  labelIds: string[];
  listUnsubscribe: boolean;
  autoSubmitted?: boolean;
  calendarMethod?: string | null;
  bodyText: string;
}

export interface GmailThreadData {
  threadId: string;
  account: string;
  messages: GmailMessageMeta[];
}

export interface ThreadLoopSpec {
  loopType: 'unanswered_inbound' | 'unanswered_outbound';
  counterpartyEmail: string;
  summary: string;
  evidence: LoopEvidence[];
  lastActivityMs: number;
  openedMs: number;
}

export interface ThreadLoopVerdict {
  open: ThreadLoopSpec[];
  close: Array<'unanswered_inbound' | 'unanswered_outbound'>;
  held?: { untilMs: number; spec: ThreadLoopSpec };
}

interface LoopDetect {
  detectThreadLoop(
    thread: GmailThreadData,
    myAddresses: Set<string>,
    now: Date,
    suppressions?: SuppressionSet,
  ): ThreadLoopVerdict;
  isAcknowledgementOnly(text: string): boolean;
}

interface LoopsStore {
  loadSuppressions(engine: BrainEngine, sourceId: string): Promise<SuppressionSet>;
  upsertOpenLoop(
    engine: BrainEngine,
    loop: OpenLoopUpsert,
  ): Promise<{ id: number; created: boolean; applied: boolean }>;
  listOpenLoops(
    engine: BrainEngine,
    opts?: {
      sourceIds?: string[];
      status?: OpenLoopRow['status'];
      loopType?: LoopType;
      counterparty?: string;
      limit?: number;
    },
  ): Promise<OpenLoopRow[]>;
}

const coreDir = dirname(fileURLToPath(import.meta.resolve('gbrain/engine')));
const store = (await import(join(coreDir, 'loops', 'loops-store.ts'))) as LoopsStore;

const detect = (await import(join(coreDir, 'google', 'loop-detect.ts'))) as LoopDetect;

export const detectThreadLoop = detect.detectThreadLoop;
export const isAcknowledgementOnly = detect.isAcknowledgementOnly;
export const loadSuppressions = store.loadSuppressions;
export const upsertOpenLoop = store.upsertOpenLoop;
export const listOpenLoops = store.listOpenLoops;

export interface CloseResult {
  closed: boolean;
  id?: number;
  status?: string;
  fact_expired?: boolean;
  retryable?: boolean;
  reason?: string;
}

/**
 * Close a loop through gbrain's own `loops_close` operation (public
 * "gbrain/operations" export), so a commitment loop with a fact_id retires its
 * fact exactly as `gbrain loops done` does. Trusted-local context (remote:false).
 */
export async function closeLoop(
  engine: BrainEngine,
  sourceId: string,
  id: number,
  note: string,
  status: 'done' | 'dropped' = 'done',
): Promise<CloseResult> {
  const op = operationsByName['loops_close'];
  if (!op) throw new Error('gbrain has no loops_close operation (gbrain upgrade changed it?)');
  const logger = {
    info: (m: string) => console.log(m),
    warn: (m: string) => console.warn(m),
    error: (m: string) => console.error(m),
  };
  const ctx = {
    engine,
    config: { engine: engine.kind },
    logger,
    dryRun: false,
    remote: false,
  };
  return (await op.handler(ctx as never, {
    id,
    status,
    note: note.slice(0, 200),
    source_id: sourceId,
  })) as CloseResult;
}

// ── Public AI gateway ("gbrain/ai/gateway") + sanitizer ──────────────────────

/**
 * Configure the gateway in this (non-gbrain-serve) process: file/env plane
 * first (GBRAIN_CHAT_MODEL, provider keys), then the DB plane (`models.*`
 * config rows) so the add-on sees the same models as the gbrain container.
 * Never throws; a failure just leaves the gateway unavailable (callers fail open).
 */
export async function setupGateway(engine: BrainEngine): Promise<void> {
  try {
    const gw = await import('gbrain/ai/gateway');
    gw.configureGatewayIfUninitialized();
    await gw.reconfigureGatewayWithEngine(engine);
  } catch (e) {
    console.warn('addon: AI gateway setup failed (reply check will fail open):', e instanceof Error ? e.message : e);
  }
}

/** gbrain's own cheap tier for small judgments: the gateway's expansion (utility) model. */
export async function defaultCheapModel(): Promise<string | undefined> {
  try {
    const gw = await import('gbrain/ai/gateway');
    return gw.getExpansionModel();
  } catch {
    return undefined;
  }
}

/** One chat completion through gbrain's gateway; returns the text. Throws on unavailable/refusal/truncation. */
export async function gatewayChat(req: { system: string; user: string; model?: string; maxTokens: number }): Promise<string> {
  const gw = await import('gbrain/ai/gateway');
  if (!gw.isAvailable('chat', req.model)) throw new Error('gbrain chat provider unavailable (no configured model / API key)');
  const res = await gw.chat({
    model: req.model,
    system: req.system,
    messages: [{ role: 'user', content: req.user }],
    maxTokens: req.maxTokens,
    temperature: 0,
  } as never);
  if (res.stopReason === 'refusal' || res.stopReason === 'content_filter') throw new Error(`model ${res.stopReason}`);
  if (res.stopReason === 'length') throw new Error('model output truncated');
  return res.text;
}

/** gbrain's prompt-injection scrubber (src/core/think/sanitize.ts, loaded by path); identity if it cannot load. */
export async function loadInjectionScrubber(): Promise<(text: string) => string> {
  try {
    const m = (await import(join(coreDir, 'think', 'sanitize.ts'))) as {
      INJECTION_PATTERNS: Array<{ rx: RegExp; replacement: string }>;
    };
    return (text) => {
      let out = text;
      for (const p of m.INJECTION_PATTERNS) out = out.replace(p.rx, p.replacement);
      return out;
    };
  } catch {
    return (t) => t;
  }
}
