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
import { JOB_OPEN, JOB_RECHECK, loopOpen, loopRecheck } from './jobs.ts';
import { makeHandler } from './http.ts';

async function main() {
  const config = loadConfig();
  if (!config) throw new Error('gbrain config not found (set up gbrain / DATABASE_URL first)');
  const engine = await createEngine(toEngineConfig(config));
  await engine.connect(toEngineConfig(config));

  const worker = new MinionWorker(engine, { queue: process.env.ADDON_QUEUE ?? 'addon' });
  worker.register(JOB_OPEN, async (ctx) => loopOpen(engine, ctx.data));
  worker.register(JOB_RECHECK, async (ctx) => loopRecheck(engine, ctx.data));
  // HTTP front for in-cluster callers that cannot queue jobs (remote MCP only).
  const port = Number(process.env.ADDON_HTTP_PORT ?? 3140);
  const handler = makeHandler(engine, {
    token: process.env.ADDON_HTTP_TOKEN || undefined,
    defaultSource: process.env.ADDON_DEFAULT_SOURCE || 'google',
  });
  Bun.serve({ port, fetch: handler });
  console.log(`gbrain-addon http listening on :${port}`);
  // start() only after every handler is registered.
  await worker.start();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
