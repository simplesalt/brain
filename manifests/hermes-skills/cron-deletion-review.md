Deletion review: judge how much was lost when these Notion items were moved to the trash.

The script output above lists trashed Notion items as JSON. Each has the text it held (`content_excerpt`), how many child pages or rows it had, who deleted it and when, its original parent, `replacement_candidates` (live pages that may replace it) and `explanation_candidates` (comments on it or its parent that may say why it was removed). You only read and judge; a script computes the risk and keeps the log. Work through every item, using only what the batch shows:

- `L` (0 to 1): how much of the deleted content's information is NOT present in the replacement candidates or anywhere else shown. 0 means all of it is still there, 1 means none of it is. An empty or placeholder item scores near 0.
- `R` (0 to 1): how clearly a live page replaces the deleted item. 1 means a live page plainly covers the same subject; 0 means nothing does.
- `E` (0 to 1): how clearly a reason for the removal is recorded in the comments or the parent. 1 means a comment or the parent says why it was removed; 0 means nothing does.
- `reason`: one sentence saying what you based the three scores on.

Do not call any tool, do not write, restore or change anything, and do not send any message.

Answer with a line reading exactly `DELETION-REVIEW-RESULT`, then a JSON list with one object per item, using the item's `id` (an empty list if the batch is empty):

```json
[
  {"id": "<item id>", "L": 0.0, "R": 0.0, "E": 0.0, "reason": "<one sentence>"}
]
```
