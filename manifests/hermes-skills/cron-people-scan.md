People scan: pick out the real people in these meetings and email threads, so gbrain gets a page for each one.

The script output above lists meetings and email threads from gbrain as JSON. You only read and judge; a script writes the pages from your answer. Work through every item before answering:

1. Read it with `mcp__gbrain__get_page` (`{"slug": "<item slug>"}`).
2. For each address in its `people`, decide whether it is a real person Dylan deals with: a colleague, client, prospect, partner, investor, or someone he met, wrote to, or answered. Leave out cold sales pitches he never answered, bulk or transactional mail from companies, shared inboxes, and anyone you cannot tell apart from a namesake.
3. For each person you keep, check whether gbrain already has them: `mcp__gbrain__search` with their email address, then with their name. Note the slug of a person page whose name and email or company match.

Call one tool at a time. Do not write, tag or change anything, do not search the web, and do not send any message.

Answer with a line reading exactly `PEOPLE-SCAN-RESULT`, then a JSON list with one object per person you kept (an empty list if none):

```json
[
  {
    "item": "<item slug>",
    "title": "<item title>",
    "date": "<item date, YYYY-MM-DD>",
    "name": "<Full Name>",
    "email": "<their email>",
    "company": "<their company, or null>",
    "about": "<one sentence: who they are, what they or their company do, and how Dylan knows them, from this item only, in words someone would search for>",
    "existing_slug": "<slug of their gbrain person page, or null>",
    "summary": "<under 15 words: met at or emailed about what>",
    "dylan_met_or_wrote": true
  }
]
```
