Meeting ingest: turn one recorded Fireflies call into a worked-up meeting page in gbrain.

The script output above names one call page in gbrain (a slug under `calls/`). Load gbrain's `meeting-ingestion` skill and follow it for that call. The call page is already the meeting page and holds the full transcript, so skip the skill's steps to normalize the input, split recordings, check for duplicates across recorders, and file a separate transcript page.

1. Read the page with `mcp__gbrain__get_page` (`{"slug": "<call slug>"}`). Note its `revision`. If it has no transcript, stop and answer `SKIPPED: no transcript`.
2. Rewrite the page with `mcp__gbrain__put_page`, passing the `revision` you read as `expected_revision`. Keep the frontmatter, the Attendees section and the whole Transcript exactly as they are. Above the Transcript, write these sections from the transcript only:
   - Summary: the key outcomes.
   - Key Decisions: each decision with its context.
   - Action Items: each task with its owner and, when someone said one, its due date.
   - Notable Quotes: only word-for-word quotes from the transcript.
   Keep the existing Fireflies summary, action items and keywords as they are; if the transcript disagrees with them, say so in your sections rather than editing theirs. Where a section has nothing, say why in one line. Never invent a decision, owner, date or quote, and if you cannot tell who said something, mark it unknown instead of guessing. On `revision_conflict`, read the page again, merge, and retry once.
3. For each attendee and each company discussed, check gbrain first (`mcp__gbrain__search`, by email, then by name). Add a timeline entry about this call with `mcp__gbrain__add_timeline_entry`, and link the page to the call with `mcp__gbrain__add_link`. When a person or company has no page, create a short one from what the call itself says, using `mcp__gbrain__put_page`. Follow the skill's rule that a claim about a role, ownership or a deal needs the exact words in the transcript. When a claim contradicts what gbrain already knows, leave it off the entity page and note the conflict in your answer.
4. A page where the people are marked "Test contact" is processed like any other. Never contact, message or look up such a person outside gbrain.

Call one tool at a time. Do not search the web, do not use any outside source, and do not send any message to anyone.

Answer with one line: `MEETING-INGEST-DONE <call slug>: <n> action items, <n> people and companies updated`, followed by one line for each conflict or claim you held back, if any.
