# gbrain-addon

Host-side add-on for the pinned gbrain (`23fdb464`, v0.60.214.0). It registers two Minion
jobs that put non-Gmail conversations (Fireflies meetings, LinkedIn, SMS, manual) into
gbrain's open-loops store. It never modifies or vendors gbrain code. It is not a Kubernetes
manifest; it is baked into the image alongside gbrain.

Run: `bun run src/worker.ts` (own worker on queue `addon`, or `ADDON_QUEUE`). gbrain at this
pin has no hook for `gbrain jobs work` to load host handlers, hence the separate worker.
Submit with `gbrain jobs submit <name> --queue addon --params '<json>'`.

## Jobs

`addon-loop-open` params: `source_id`, `channel` (`fireflies|linkedin|sms|manual`),
`external_id`, `thread_id?`, `loop_type` (a gbrain LoopType), `counterparty_slug?`,
`counterparty_email?`, `summary`, `due_at?` (ISO), `evidence: [{message_id?, page_slug?, quote?}]`.
Upserts one loop, detector `manual`, dedup key `channel:external_id:loop_type`, thread id
`channel:<thread_id or external_id>` (never collides with Gmail ids). Re-submitting is idempotent.

`addon-loop-recheck` params: `source_id`, `counterparty_slug`,
`messages: [{channel, message_id, page_slug?, from_me, sent_at (ISO), text}]`.
Lists that person's open loops (matched by `counterparty_slug`). An `unanswered_inbound` loop is
closed by a later `from_me` message, an `unanswered_outbound` loop by a later inbound one
("later" = after the loop's last activity). The close note is `settled by channel:message_id`.
Commitment and decision loops stay open and are listed in `needs_review`. Closing goes through
gbrain's `loops_close` operation, so a fact-linked loop retires its fact as `gbrain loops done` does.
Result: `{closed, left_open, needs_review}`.

## HTTP endpoint

The worker process also serves HTTP (`Bun.serve`, port `ADDON_HTTP_PORT`, default 3140), because
in-cluster callers reach gbrain only over remote MCP, which cannot queue these jobs.

- `GET /health` -> `{"ok":true}`
- `POST /loops/open` -> body = `addon-loop-open` params; runs `loopOpen` synchronously, returns its result
- `POST /loops/recheck` -> body = `addon-loop-recheck` params; returns `loopRecheck`'s result

`source_id` defaults to `ADDON_DEFAULT_SOURCE` (default `google`) when omitted. When
`ADDON_HTTP_TOKEN` is set, requests need `Authorization: Bearer <token>` (401 otherwise). Errors:
400 `{"error"}` for bad params or invalid JSON, 404 unknown path, 405 wrong method, 413 body over
256 KB, 500 `{"error"}` otherwise. The handler is `makeHandler(engine, opts)` in `src/http.ts`.

## Upgrading gbrain

All gbrain-internal imports are in `src/gbrain-internal.ts`: `src/core/loops/loops-store.ts`
(`upsertOpenLoop`, `listOpenLoops`; loaded by path) and the public `gbrain/operations`
`operationsByName.loops_close`. After bumping the pin in `package.json`, re-read those, then run
`bun install && bun test` (tests run on gbrain's PGLite engine).
