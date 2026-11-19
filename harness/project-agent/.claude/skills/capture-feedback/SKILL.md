---
name: capture-feedback
description: Use when the person says "record this conversation" or gives feedback worth keeping on the session as a whole, rather than a single problem. Distill the session into at most five observations and invoke this skill once per observation with that observation as args (first line `<problem|opportunity|unexpected-outcome|tracked-result>: <title>`, then 3-10 lines); a hook writes each to the pod's stdout as an error log line, where the logs MCP picks it up.
---

<!-- agent-prompt -->


The observation you passed as args is already written: a hook logged it to the pod's
stdout as one JSON line with `"level": "error"`, `"logger": "agent-report"`, the skill
name and your session label, before this text loaded. A denied call recorded nothing;
its reason says what to fix, so invoke `/capture-feedback` again with the corrected args.

1. Distill the session into at most five observations: durable findings about how the
   work went — a problem, opportunity, unexpected outcome, or tracked result — each
   naming the piece of the system responsible and the proposed fix. Put the person's own
   feedback, in their words, in the first one.
2. Invoke `/capture-feedback` once per observation, with that observation as args:
   - First line: `<problem|opportunity|unexpected-outcome|tracked-result>: <title>`.
   - Then 3-10 lines, an external record of what happened and what was concluded, not
     an account of your own reasoning — that framing is what a `reasoning_extraction`
     refusal keys on.
3. Tell the person how many observations were recorded and their titles.

Never pass the transcript or mechanical detail (JSONL structure, tool_use ids, message
counts, restatements of the transcript) as an observation.

If a distillation is refused, reissue it asking for the facts and conclusion, not the
reasoning that produced them.
