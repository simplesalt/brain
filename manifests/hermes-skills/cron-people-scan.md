People scan: give gbrain a page for every real person Dylan meets or emails, linked to where he met them.

The script output above lists recent meetings and email threads from gbrain as JSON. Work through every item, in order:

1. Read it with `mcp_gbrain_get_page` (`{"slug": "<item slug>"}`).
2. For each address in its `people`, decide whether it is a real person Dylan deals with: a colleague, client, prospect, partner, investor, or someone he met, wrote to, or answered. Skip cold sales pitches he never answered, bulk or transactional mail from companies, shared inboxes, and anyone you cannot tell apart from a namesake.
3. For each person you keep, find their page: `mcp_gbrain_search` with their email address, then with their name. Use an existing person page when the name and email or company match.
   - No page: create one with `mcp_gbrain_put_page`, slug `people/<first>-<last>` in lowercase (add the company if that slug is taken by someone else). Use this content, filling only what the item states:

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

     <One or two sentences: who they are, what they do, and how Dylan knows them, taken only from this item.>

     Source: <item title>, <item date>.
     ```
   - Page exists: do not rewrite it. Add `mcp_gbrain_add_timeline_entry` with `{"slug": "<person slug>", "date": "<item date>", "summary": "<met or emailed about what>", "source": "<item slug>"}`.
4. Link the person to the item: `mcp_gbrain_add_link` with `{"from": "<person slug>", "to": "<item slug>", "link_type": "attended"}` for a meeting or `"link_type": "emailed"` for email, and a short `context`.
5. When the item is done, even if nobody qualified, mark it: `mcp_gbrain_add_tag` with `{"slug": "<item slug>", "tag": "people-scanned"}`.

Rules: use only gbrain tools. Do not search the web, do not send any message or email, and do not change meeting or email pages beyond the tag. If a call fails, fix its arguments from the error and continue with the next item.

Finish with one line per item: `<item slug>: created <names>; updated <names>; skipped <reason>`.
