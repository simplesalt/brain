---
name: hermes-miss-review
description: Use for the daily review of Hermes's chats — reads the last day of saved Hermes chats, finds misses in Hermes's reasoning, scores them and files them under failure patterns in the Notion "Hermes misses" tally. Also use when asked to run that review by hand.
---

# Daily Hermes miss review

Goal: every miss in Hermes's reasoning over one UTC day lands in Notion, filed under a failure pattern, scored, so the patterns with the highest importance get fixed first and a fix can show a measurable drop.

Arguments: an optional UTC date `YYYY-MM-DD`. Default: yesterday (UTC). A run by hand on the same day may pass today's date.

## Where things are

- Chats: every Hermes message, uncut with full reasoning, is in the hermes-chats database, read with curl from the read API `http://hermes-chats.ssint-main-ai:8080` (`manifests/hermes-chats.yaml`):
  - `GET /days/YYYY-MM-DD` lists the day's chats with message counts, content/reasoning sizes and flags (`user_fail`, `miss_report`, `tool_error`). Start with flagged chats.
  - `GET /sessions/<id>` gives each message's head/tail of reasoning and content, lengths and flags; enough for most chats.
  - `GET /messages/<session>/<msg>?part=reasoning&offset=0&limit=20000` reads one message's full reasoning in chunks. Reasoning can run to tens of thousands of lines: read it chunk by chunk and keep only the step where it went wrong; never load a whole day's reasoning at once.
  - `GET /search?q=...&from=...&to=...` finds earlier occurrences of a pattern.
  - `POST /notes` with `{"session_id","msg_id","pattern","quote","note"}` caches the where-it-went-wrong quote for each miss you file; do this for every miss.
- Tally (Notion, under Team Dashboard › Hermes misses, https://www.notion.so/408e6a5b97798173b662fea97c61c2c5):
  - Failure patterns, data source `collection://76bf44dc-faae-445d-8f28-9c214da52de2`: Pattern (title), Category, Description, Status, Fix effort, Fix shipped, First seen. Importance, Last 7 days, Occurrences, Last seen and the before/after-fix numbers are computed; never write them.
  - Misses, data source `collection://0f38d5c3-5255-46e1-bb32-631995eaabe4`: Miss (title), Pattern (relation), Date, Impact, Cost, What Hermes reasoned, Should have, Chat, Review run.

## Catch-up for days before the store existed

The hermes-chats store starts on 2026-10-29 at about 12:52 UTC. For earlier chats, Hindsight's `hermes` bank keeps one document per Hermes API chat, id `api-<hex>`, tag `session:<id>`: `HINDSIGHT_BANK_ID=hermes ~/.claude/scripts/hindsight-get-document.sh <id> --text-only` returns a JSON list of `{role, content, timestamp}`. List them with `hs_request GET "$(hs_bank_path 'documents?limit=500')"` (after `export HINDSIGHT_BANK_ID=hermes; source ~/.claude/scripts/lib-hindsight.sh`) and pick by `updated_at`. These hold only user and assistant text, with no reasoning or tool calls: judge the reasoning from what Hermes said, and say in the summary that these were reviewed from text only. Read only; never write to or delete from the `hermes` bank.

## Steps

1. Load the day's chats from `/days/<date>` (no chats or the API down: say so and stop). Skip chats that are Hermes's own scheduled jobs only if they show no reasoning.
2. Read each chat focusing on Hermes's reasoning and tool use, not on how polite the answer was. A miss is a point where Hermes's reasoning went wrong in a way a better agent would have avoided, for example:
   - claimed a limit, error or missing access that was not real, or quoted an error and passed on a fix the user should not do (e.g. the phantom Dux-Soup gbrain source error on 2026-10-29);
   - gave up, or asked the user, when another tool, argument or approach was available;
   - invented or assumed a fact instead of checking gbrain, Hindsight, Notion or the tool;
   - wrong tool, wrong or missing arguments, or retried an identical failing call;
   - ignored an instruction in AGENTS.md, SOUL.md or a skill;
   - lost context it had, or acted on a stale or unrelated memory;
   - said it did something it did not do, or reported success on a failed call.
   User reports always count. A user message containing `#fail`, a plain "hard fail / you suck / that's completely wrong", or a `HERMES MISS REPORT` block from Hermes (written by its `miss-report` skill) is a definite miss even when Hermes's reasoning looked fine: find the turn it refers to and analyse why the reasoning went wrong (often answering a different question or doing the wrong task). Use the report's Expected / Got for "Should have" and the user's Severity as Impact (keep "(estimated)" severities only if the chat supports them); mark the Miss title with "[user report]". If the user flagged a failure but no report block followed, file it anyway from the conversation.
   Do not count a model or network failure that Hermes's reasoning could not have avoided; do not count the same miss twice within one chat.
3. Score each miss with the scales on the Hermes misses page: Impact 1-5 (1 cosmetic or self-corrected … 5 harm outside the chat) and Cost 1-5 (user time or rework: 1 none … 5 hours or money). Judge from what the user did next in the chat.
4. Match each miss to a pattern. Fetch all Failure patterns rows first (query the data source). Reuse a pattern when the underlying cause is the same even if the topic differs; a pattern is a cause ("Treats a permission_denied for an unknown source as missing access"), not an incident. Create a new pattern only when none fits, with Category, a one-line Description of what Hermes does wrong and what it should do, Status Open and First seen = the chat date. Keep patterns few and general enough to recur; if two existing patterns are really one, say so in the summary rather than merging them yourself.
5. Before writing, query Misses for rows with this Date and the same Chat to avoid filing a miss twice when a day is rerun.
6. Create one Misses row per miss: Miss (short title), Pattern, Date (the chat date), Impact, Cost, What Hermes reasoned (a short quote or paraphrase of the reasoning that went wrong), Should have (what it should have done), Chat (`<session id> — <title>`), Review run (today).
7. If a pattern has Status "Fixed - watching" or "Resolved" and gets a new miss, keep its Status but mention it in the summary: the fix may not have worked.

## Report

End with a short summary, in plain words, for Dylan: how many chats were reviewed, how many misses were filed and under how many patterns (how many new), the top three patterns by score over the last 7 days (sum Impact x Cost of their Misses dated in the last 7 days; the computed Notion columns are not readable through the API), and any pattern with a shipped fix that recurred. Link the Hermes misses page. If the day had no saved chats or the read API was down, say that instead.

## Linking dev efforts

A dev effort that fixes a pattern puts the pattern's Notion link in its What, and an indicator of the form "Weekly after fix for pattern HP-<n> falls below <x> (Weekly before fix: <y>) within 2 weeks of shipping". When it ships, set the pattern's Fix effort (the issue URL), Fix shipped (date) and Status "Fixed - watching". The review never sets these itself.
