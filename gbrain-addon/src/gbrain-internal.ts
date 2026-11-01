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
  due_at: string | null;
  status: 'open' | 'done' | 'dropped' | 'stale';
  detector: string;
  fact_id: number | null;
  opened_at: string;
  last_activity_at: string;
  closed_at: string | null;
  closed_by: string | null;
}

interface LoopsStore {
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
    status: 'done',
    note: note.slice(0, 200),
    source_id: sourceId,
  })) as CloseResult;
}
