---
name: capture-feedback
description: Use when the person says "record this conversation" or gives feedback worth keeping on the session as a whole, rather than a single problem — stores one transcript document plus a handful of distilled observations.
---

<!-- agent-prompt -->


1. `capture-session.sh <title>` once, to store this session's transcript as a single
   document. `$HINDSIGHT_BANK_ID` and `$CLAUDE_CODE_SESSION_ID` are picked up
   automatically. Report the printed `document_id` and `memory_unit_count`.
2. Distill the session into at most five observations: durable findings about how the
   work went — a problem, opportunity, unexpected outcome, or tracked result — each
   naming the piece of the system responsible and the proposed fix. Each observation is
   3-10 lines. Write each as an external record of what happened and what was
   concluded, not an account of your own reasoning — that framing is what a
   `reasoning_extraction` refusal keys on.
3. Ingest each observation separately:
   `<content> | hindsight-ingest.sh <title> --tags observation,<workstream bank> --meta type=<problem|opportunity|unexpected-outcome|tracked-result> --meta workstream=<bank>`
   (the bank's default `observation` strategy applies).

Never ingest the transcript itself through `hindsight-ingest.sh` — that is exactly the
mechanical-noise failure this skill replaces. Never turn mechanical detail (JSONL
structure, tool_use ids, message counts, restatements of the transcript) into an
observation; it belongs in the transcript document from step 1 only.

If a distillation or ingest is refused, reissue it asking for the facts and conclusion,
not the reasoning that produced them. If the session can't respond at all, a peer
session reads its transcript from the session's project directory and files the
observations on its behalf.
