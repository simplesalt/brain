/**
 * HTTP front for the add-on jobs. In-cluster callers reach gbrain only over
 * remote MCP, which cannot queue Minion jobs, so the worker process also serves
 * these endpoints. The handler is a plain (Request) => Response function so
 * tests can call it without opening a port.
 *
 *   GET  /health         -> {"ok":true}
 *   POST /loops/open     -> loopOpen(engine, body)
 *   POST /loops/recheck  -> loopRecheck(engine, body)
 *   POST /loops/thread   -> loopThread(engine, body, opts.thread)
 */
import type { BrainEngine } from 'gbrain/engine';
import { loopOpen, loopRecheck, loopThread, type ThreadDeps } from './jobs.ts';
import { AddonParamError } from './validate.ts';

export const MAX_BODY_BYTES = 256 * 1024;

export interface HandlerOpts {
  /** When set, requests must carry `Authorization: Bearer <token>`. */
  token?: string;
  /** source_id used when a body omits it. */
  defaultSource?: string;
  /** Reply-needed gate for /loops/thread (see ThreadDeps). */
  thread?: ThreadDeps;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

export function makeHandler(engine: BrainEngine, opts: HandlerOpts = {}) {
  const defaultSource = opts.defaultSource ?? 'google';
  const routes: Record<string, (engine: BrainEngine, data: unknown) => Promise<unknown>> = {
    '/loops/open': loopOpen,
    '/loops/recheck': loopRecheck,
    '/loops/thread': (e, d) => loopThread(e, d, opts.thread),
  };

  return async (req: Request): Promise<Response> => {
    try {
      const path = new URL(req.url).pathname;
      const isHealth = path === '/health';
      const run = routes[path];
      if (!isHealth && !run) return json(404, { error: 'not found' });

      if (opts.token && req.headers.get('authorization') !== `Bearer ${opts.token}`) {
        return json(401, { error: 'unauthorized' });
      }

      if (isHealth) {
        if (req.method !== 'GET') return json(405, { error: 'method not allowed' });
        return json(200, { ok: true });
      }
      if (req.method !== 'POST') return json(405, { error: 'method not allowed' });

      const declared = Number(req.headers.get('content-length') ?? 0);
      if (declared > MAX_BODY_BYTES) return json(413, { error: 'body too large' });
      const text = await req.text();
      if (Buffer.byteLength(text) > MAX_BODY_BYTES) return json(413, { error: 'body too large' });

      let body: unknown;
      try {
        body = JSON.parse(text);
      } catch {
        return json(400, { error: 'invalid JSON' });
      }
      if (body && typeof body === 'object' && !Array.isArray(body)) {
        const rec = body as Record<string, unknown>;
        if (rec.source_id === undefined) body = { ...rec, source_id: defaultSource };
      }
      return json(200, await run(engine, body));
    } catch (e) {
      if (e instanceof AddonParamError) return json(400, { error: e.message });
      console.error('addon http error', e);
      return json(500, { error: e instanceof Error ? e.message : String(e) });
    }
  };
}
