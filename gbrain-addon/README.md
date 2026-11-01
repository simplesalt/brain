# gbrain-addon

Host-side add-on for the pinned gbrain (`23fdb464`, v0.60.214.0). It registers three Minion
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
A `from_me` acknowledgement-only message ("Thanks!", gbrain's `isAcknowledgementOnly`) settles
nothing and is listed in `ignored`. Result: `{closed, left_open, needs_review, ignored}`.

`addon-loop-thread` params: `source_id`, `channel`, `thread_id`, `counterparty_slug`,
`counterparty_name?`, `now?` (ISO, tests), `messages: [{message_id, from_me, sent_at (ISO), text}]`
(the whole conversation, any order). Assesses a non-email conversation the way gbrain assesses
email, by running gbrain's `detectThreadLoop` on a synthetic Gmail thread (me =
`me@<channel>.invalid`, them = `<slug>@<channel>.invalid`). One loop per conversation and type:
`unanswered_inbound` (reply owed, after 24h) or `unanswered_outbound` (my question unanswered, after
72h), detector `manual` (so gbrain's Gmail sweeps never touch it), dedup key
`channel:thread_id:loop_type`, thread id `channel:thread_id`. A turn flip closes the loop through
`loops_close` (`settled by channel:message_id`). Inside a grace window nothing opens and the result
carries `held_until` (ISO); the caller re-posts the conversation then. Source suppressions
(`gbrain loops mute`) apply when keyed on the lowercase `channel:thread_id` or the synthetic sender
address. Result: `{opened, closed, held_until?, verdict}`, verdict one of `reply_owed`,
`waiting_on_them`, `held`, `settled`, `nothing_owed`.

## Reply-needed check

Cold-pitch rule (prompt version 2): the judge input carries `prior_contact` (true / false / null) and the `<thread>`
header shows `prior_contact=true|false|unknown`. With `false`, unsolicited outreach (selling, vendor or partnership
pitches, event or webinar invitations, recruiting, "quick question" prospecting) needs no reply even when it asks a
question; `null` judges as before. Gmail loops: true if the thread page has a message from me (a `## → ` heading),
else true if another email page in the source lists my account (frontmatter `account`) in `senders` and the
counterparty in `participants`; false otherwise; null if account or counterparty email is unknown. Channel
conversations (`/loops/thread`): true if any supplied message is `from_me`. Post-pass loops built from evidence quotes
only: null. `PROMPT_VERSION` (`src/reply-check.ts`) is stored per verdict in `prompt_version` (added with
`ADD COLUMN IF NOT EXISTS`, old rows = 1); open loops with an older stored version are re-judged by the post-pass.

gbrain opens `unanswered_inbound` ("reply owed") loops from mechanical rules, so receipts, shipping
notices, pitches and FYIs become loops. The add-on adds a model judgment, no gbrain code changes.

`judgeReplyNeeded(input, deps?)` (`src/reply-check.ts`) makes one strict-JSON call through gbrain's own AI
gateway (public export `gbrain/ai/gateway`, `chat()`; the same call loops_extract makes) and returns
`{needs_reply, reason (<=120 chars), confidence 0..1}`. The thread is wrapped as DATA and scrubbed with gbrain's
injection patterns. A model error or unparsable answer returns `unknown: true` with `needs_reply: true`,
confidence 0: callers keep the loop (fail open). The model call is injectable (`deps.call`) for tests.

Model: `ADDON_REPLY_CHECK_MODEL`, else gbrain's cheap utility tier (`getExpansionModel()`, i.e. the DB
`models.expansion` setting / `GBRAIN_EXPANSION_MODEL`, which the cluster sets to `GBRAIN_LIVE_DUMB_MODEL`).

Post-pass (`reviewNewLoops`, `src/reply-review.ts`), run by the worker every `ADDON_REPLY_CHECK_INTERVAL_MS`
(60000): takes open `unanswered_inbound` loops of `ADDON_DEFAULT_SOURCE` not yet judged at their current
`last_activity_at` (table `addon_reply_checks(loop_id, last_activity_at, verdict, reason, checked_at, prompt_version)`, created
with `CREATE TABLE IF NOT EXISTS`; an `unknown` verdict is retried after 15 min). Gmail loops are judged on the
email thread page (`engine.getPage(page_slug, {sourceId}).compiled_truth`); our own channel loops
(`linkedin:`, `fireflies:`, `sms:`, `manual:` thread ids) on their evidence quotes. `needs_reply=false` with
confidence >= `ADDON_REPLY_CHECK_MIN_CONFIDENCE` (0.7) drops the loop via `loops_close` status `dropped`, note
`no reply needed: <reason>`. At most `ADDON_REPLY_CHECK_MAX_PER_PASS` (25) model calls per pass; one summary
line is logged per pass. gbrain reopens a dropped loop only on genuinely newer thread activity, which changes
`last_activity_at`, so the loop is judged again then. `ADDON_REPLY_CHECK=off` disables the pass and the gate.

Gate: `/loops/thread` / `addon-loop-thread` runs the judge before opening an `unanswered_inbound` loop. On "no reply
needed" nothing opens and the result is `verdict: "skipped_by_reply_check"` with `skipped_by_reply_check: <reason>`.

The addon-worker container needs the gateway's env: `OPENROUTER_API_KEY` (or whichever provider key the
model needs), `GBRAIN_CHAT_MODEL`, `GBRAIN_EXPANSION_MODEL` (or `ADDON_REPLY_CHECK_MODEL`), plus the
`GBRAIN_LIVE_DUMB_MODEL`/`GBRAIN_SLOW_*` values if gbrain's config references them. Same values as the gbrain container.

## HTTP endpoint

The worker process also serves HTTP (`Bun.serve`, port `ADDON_HTTP_PORT`, default 3140), because
in-cluster callers reach gbrain only over remote MCP, which cannot queue these jobs.

- `GET /health` -> `{"ok":true}`
- `POST /loops/open` -> body = `addon-loop-open` params; runs `loopOpen` synchronously, returns its result
- `POST /loops/recheck` -> body = `addon-loop-recheck` params; returns `loopRecheck`'s result
- `POST /loops/thread` -> body = `addon-loop-thread` params; returns `loopThread`'s result

`source_id` defaults to `ADDON_DEFAULT_SOURCE` (default `google`) when omitted. When
`ADDON_HTTP_TOKEN` is set, requests need `Authorization: Bearer <token>` (401 otherwise). Errors:
400 `{"error"}` for bad params or invalid JSON, 404 unknown path, 405 wrong method, 413 body over
256 KB, 500 `{"error"}` otherwise. The handler is `makeHandler(engine, opts)` in `src/http.ts`.

## Upgrading gbrain

All gbrain-internal imports are in `src/gbrain-internal.ts`: `src/core/loops/loops-store.ts`
(`upsertOpenLoop`, `listOpenLoops`, `loadSuppressions`; loaded by path), `src/core/google/loop-detect.ts`
(`detectThreadLoop`, `isAcknowledgementOnly`; loaded by path) and the public `gbrain/operations`
`operationsByName.loops_close`, the public `gbrain/ai/gateway`, and `src/core/think/sanitize.ts` (injection patterns; loaded by path). After bumping the pin in `package.json`, re-read those, then run
`bun install && bun test` (tests run on gbrain's PGLite engine).
