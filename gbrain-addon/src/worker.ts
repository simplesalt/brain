/**
 * Worker entry: a standalone Minion worker on its own queue.
 *
 * gbrain v0.60.214.0 has NO host entry-point hook in `gbrain jobs work`
 * (registerBuiltinHandlers is closed; only GBRAIN_PLUGIN_PATH exists and it
 * loads subagent definitions, not job handlers), so the add-on runs as its own
 * process. Submit jobs with `--queue addon` (default; override with ADDON_QUEUE).
 */
import { MinionWorker } from 'gbrain/minions';
import { createEngine } from 'gbrain/engine-factory';
import { loadConfig, toEngineConfig } from 'gbrain/config';
import { JOB_OPEN, JOB_RECHECK, JOB_THREAD, loopOpen, loopRecheck, loopThread } from './jobs.ts';
import { makeHandler } from './http.ts';
import { setupGateway } from './gbrain-internal.ts';
import { judgeReplyNeeded, minConfidence, replyCheckEnabled } from './reply-check.ts';
import { reviewNewLoops } from './reply-review.ts';
import { MAX_GUARD_BODY_BYTES, engineStore, ensureMarkTable, makeGuardHandler } from './guard.ts';

async function main() {
  const config = loadConfig();
  if (!config) throw new Error('gbrain config not found (set up gbrain / DATABASE_URL first)');
  const engine = await createEngine(toEngineConfig(config));
  await engine.connect(toEngineConfig(config));

  // Reply-needed check (ADDON_REPLY_CHECK=off disables): model calls via gbrain's AI gateway.
  const checkOn = replyCheckEnabled();
  if (checkOn) await setupGateway(engine);
  const sourceId = process.env.ADDON_DEFAULT_SOURCE || 'google';

  const worker = new MinionWorker(engine, { queue: process.env.ADDON_QUEUE ?? 'addon' });
  worker.register(JOB_OPEN, async (ctx) => loopOpen(engine, ctx.data));
  worker.register(JOB_RECHECK, async (ctx) => loopRecheck(engine, ctx.data));
  const threadDeps = checkOn ? { judge: (i: Parameters<typeof judgeReplyNeeded>[0]) => judgeReplyNeeded(i), minConfidence: minConfidence() } : {};
  worker.register(JOB_THREAD, async (ctx) => loopThread(engine, ctx.data, threadDeps));
  // HTTP front for in-cluster callers that cannot queue jobs (remote MCP only).
  const port = Number(process.env.ADDON_HTTP_PORT ?? 3140);
  const handler = makeHandler(engine, {
    token: process.env.ADDON_HTTP_TOKEN || undefined,
    defaultSource: sourceId,
    thread: threadDeps,
  });
  Bun.serve({ port, fetch: handler });
  console.log(`gbrain-addon http listening on :${port}`);
  const guardUpstream = process.env.GUARD_UPSTREAM;
  if (guardUpstream) {
    await ensureMarkTable(engine);
    const guardPort = Number(process.env.GUARD_PORT ?? 3141);
    const guard = makeGuardHandler(engineStore(engine), { upstream: guardUpstream, defaultSource: process.env.GUARD_DEFAULT_SOURCE || 'default' });
    Bun.serve({ port: guardPort, fetch: guard, maxRequestBodySize: MAX_GUARD_BODY_BYTES });
    console.log(`gbrain-addon write guard listening on :${guardPort}, upstream ${guardUpstream}`);
  }
  if (checkOn) {
    const every = Number(process.env.ADDON_REPLY_CHECK_INTERVAL_MS ?? 60_000);
    const maxPerPass = Number(process.env.ADDON_REPLY_CHECK_MAX_PER_PASS ?? 25);
    let running = false;
    setInterval(async () => {
      if (running) return;
      running = true;
      try {
        await reviewNewLoops(engine, { judge: (i) => judgeReplyNeeded(i), sourceId, minConfidence: minConfidence(), maxPerPass });
      } catch (e) {
        console.error('reply-check pass failed', e);
      } finally {
        running = false;
      }
    }, Number.isFinite(every) && every > 0 ? every : 60_000);
    console.log(`reply-check on: every ${every}ms, cap ${maxPerPass}/pass, source ${sourceId}`);
  }
  // start() only after every handler is registered.
  await worker.start();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
