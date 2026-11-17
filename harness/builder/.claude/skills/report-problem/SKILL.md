---
name: report-problem
description: Use when recording a problem, opportunity, unexpected outcome, or tracked result worth remembering but not worth scheduling as work, as a Hindsight observation.
---

<!-- agent-prompt -->


search first: `hindsight-recall.sh <query> --types observation,world --tags observation`.
if a matching observation exists, append your specifics to it:
`<content> | hindsight-ingest.sh <same title> --append`
else create a new one:
`<content> | hindsight-ingest.sh <title> --tags observation,<workstream bank> --meta type=<problem|opportunity|unexpected-outcome|tracked-result> --meta workstream=<bank>`

Content on stdin is freeform, includes all details, and stays to about 10 lines.

Write it as evidence, sequence, and conclusion — an external record, not an account of
your own reasoning; the reasoning framing is what a `reasoning_extraction` refusal keys
on. If refused, reissue it asking for the facts and conclusion, not the reasoning that
produced them. A session that can no longer respond at all is not lost: another session
reads its transcript from its project directory and files the observation on its behalf.

If the material is a whole conversation worth keeping rather than one problem, use
`/capture-feedback` instead.
