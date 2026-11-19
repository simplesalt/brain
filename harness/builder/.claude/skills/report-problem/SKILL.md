---
name: report-problem
description: Use when recording a problem, opportunity, unexpected outcome, or tracked result worth remembering but not worth scheduling as work. Invoke it with the whole record as args (first line `<problem|opportunity|unexpected-outcome|tracked-result>: <title>`, then the details); a hook writes it to the pod's stdout as an error log line, where the logs MCP picks it up.
---

<!-- agent-prompt -->


The record you passed as args is already written: a hook logged it to the pod's stdout
as one JSON line with `"level": "error"`, `"logger": "agent-report"`, the skill name and
your session label, before this text loaded. A denied call recorded nothing; its reason
says what to fix, so invoke `/report-problem` again with the corrected args.

The args are the record:
- First line: `<problem|opportunity|unexpected-outcome|tracked-result>: <title>`.
- Then freeform, all details, about 10 lines: the workstream, evidence, sequence and
  conclusion.

Each invocation is one record; there is no append or search. To add to a record, invoke
again with the same title and the new specifics.

Write it as evidence, sequence, and conclusion — an external record, not an account of
your own reasoning; the reasoning framing is what a `reasoning_extraction` refusal keys
on. If refused, reissue it asking for the facts and conclusion, not the reasoning that
produced them.
