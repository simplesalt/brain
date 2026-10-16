---
name: catch-up
description: Use when Dylan wants to catch up on what is waiting on him (open loops, unanswered email, pending decisions, promises, or unanswered questions on his notes). Gathers everything, groups it by topic, and serves it in focused batches of 5 ordered by priority and effort, then acts on his answers.
---

# Catch up

Dylan runs this on demand to work through what is waiting on him in focused
blocks. Nothing here is pushed to him unprompted.

## 1. Gather

- Email and commitments: `mcp_gbrain_open_loops` with
  `{"source_id": "gmail", "group_by": "none", "limit": 200}`. Each loop has an
  `id`, `loop_type` (`unanswered_inbound`, `unanswered_outbound`,
  `commitment_owed_by_me`, `commitment_owed_to_me`, `decision_pending`),
  `summary`, `counterparty_email`, `due_at` and `page_slug` (the email thread).
- Note questions: `mcp_gbrain_list_pages` with `{"tag": "follow-up", "limit": 100}`;
  for each page, `mcp_gbrain_get_page` and take every `- [ ]` line under
  `## Open questions`.

Skip loops that are clearly not his to act on (receipts, automated notices,
newsletters): close them with `mcp_gbrain_mail_loops_close`
`{"id": <id>, "status": "dropped", "note": "not actionable"}` and mention the count
at the end, not one by one.

## 2. Group by topic

Put every item in exactly one cluster, the thing he would have in his head while
answering it:

- all open questions on one note page form one cluster;
- loops on the same email thread (`page_slug`) or with the same person or company
  form one cluster;
- scheduling (meeting times, invites, reschedules) forms its own cluster;
- otherwise group by shared project or company; read the thread page when the
  summary is not enough to tell.

## 3. Order

Judge each item's priority (who is waiting, how long, money or relationships at
stake, `due_at`) and expected effort (a one-line answer vs a considered reply or a
decision). Order clusters by their most urgent item, and within a cluster by
priority, then by least effort first. When you cannot judge an item's priority or
what it needs, include it and ask Dylan about it as part of the batch rather than
guessing.

## 4. Serve batches of 5

Serve 5 items at a time, all from one cluster. A cluster with more than 5 items
continues in the next batch before any other cluster starts; never leave part of
a cluster to come back later. A batch may combine small clusters only when they
are the same kind of thing (e.g. two short scheduling threads), never mixing
notes with email or scheduling.

For each item give: a number, one line on what it is and who is waiting, and what
you propose (a drafted reply, a decision to make, an answer you need). Then wait
for his answers. "Next" or "more" serves the next batch; remember what was handled
so nothing repeats.

## 5. Act on his answers

- Email reply: draft it and send with the `outbound-email` skill once he approves.
  Email loops close by themselves when the reply lands; do not close them.
- Decision or commitment handled, or one he drops:
  `mcp_gbrain_mail_loops_close` `{"id": <id>, "status": "done"}` (or `"dropped"`),
  with a short `note`.
- Note question answered: follow step 5 of the `note-capture` skill.

End each batch with what was done in one line and how many items remain.
