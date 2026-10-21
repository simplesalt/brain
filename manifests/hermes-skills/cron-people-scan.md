People scan: give gbrain a page for every real person Dylan meets or emails, linked to where he met them.

The script output above lists recent meetings and email threads from gbrain as JSON. Work through every item, in order, and do not stop or answer until each one is done; each item is handed out only once:

1. Read it with `mcp__gbrain__get_page` (`{"slug": "<item slug>"}`).
2. For each address in its `people`, decide whether it is a real person Dylan deals with: a colleague, client, prospect, partner, investor, or someone he met, wrote to, or answered. Skip cold sales pitches he never answered, bulk or transactional mail from companies, shared inboxes, and anyone you cannot tell apart from a namesake.
3. For each person you keep, find their page: `mcp__gbrain__search` with their email address, then with their name. Use an existing person page when the name and email or company match.
   - No page: create one with `mcp__gbrain__put_page`, slug `people/<first>-<last>` in lowercase (add the company if that slug is taken by someone else). Use this content, filling only what the item states:

     ```
     ---
     type: person
     title: <Full Name>
     company: <company, if clear>
     emails:
       - <email>
     aliases:
       - <email>
     ---

     # <Full Name>

     ## About

     <One sentence: who they are, what they do or what their company does, and how Dylan knows them, taken only from this item. Use the words someone would search for, e.g. "runs an AI interviewing startup".>

     Source: <item title>, <item date>.
     ```
     Then tag the new page for a LinkedIn lookup: `mcp__gbrain__add_tag` with `{"slug": "<person slug>", "tag": "needs-linkedin"}`, but only if Dylan met them or wrote to them.
   - For a new page and an existing one alike, record where Dylan met them: `mcp__gbrain__add_timeline_entry` with `{"slug": "<person slug>", "date": "<item date>", "summary": "<met at or emailed about what>", "source": "<item slug>"}`. Never rewrite an existing page.

Rules: use only gbrain tools, one call at a time. Do not search the web, do not send any message or email, and do not change meeting or email pages. If a call fails, fix its arguments from the error and continue with the next item.

Finish with one line per item: `<item slug>: created <names>; updated <names>; skipped <reason>`.
