---
name: note-capture
description: Use when Dylan sends a photo of handwritten notes (a notebook page, whiteboard, sticky note) to get into gbrain. Transcribes it, checks the transcript with him, files it as a gbrain note linked to the people and companies it mentions, and records anything unclear as open questions that the catch-up skill brings back later.
---

# Note capture

## 1. Transcribe

Read the photo the same way you read business cards. Transcribe it faithfully:
keep the page's structure (headings, bullets, arrows as "→", boxes, underlines as
emphasis), keep abbreviations as written, and do not tidy the wording. Mark any
word you cannot read as `[?]` with your best guess, e.g. `[?Kessler]`.

## 2. Check with Dylan

Show the transcript and, in the same message, list what is unclear: illegible
words, names you cannot place, ambiguous numbers or dates, and anything that reads
like a to-do with no owner or deadline. Ask him to correct the transcript.

If he answers some and not others, or says "later", do not wait: file the note
with whatever is still unclear as open questions (step 4).

## 3. Link the people, companies and projects

For each name in the note, find the existing gbrain page before linking:
`mcp_gbrain_search` with the name (add the company if the note gives one). Link to
the page you find. Never create a new person or company page from a note alone; a
name you cannot match is an open question ("Who is 'Sam' — Sam Ortiz at Acme?").

## 4. File it

Write one page with `mcp_gbrain_put_page`:

- slug: `notes/YYYY-MM-DD-<short-topic>` (the note's date if it has one, else today)
- content:

```markdown
---
type: note
title: <short topic>
date: YYYY-MM-DD
source: handwritten
tags: [handwritten]
---

# <short topic>

<corrected transcript>

## Open questions

- [ ] <question 1>
- [ ] <question 2>
```

Leave out the `## Open questions` section when nothing is unclear. When it has any
unchecked item, also call `mcp_gbrain_add_tag` with tag `follow-up` on the page.

Then link each matched person, company or project: `mcp_gbrain_add_link` from the
note's slug to the entity's slug, with `context` naming what the note says about
them.

## 5. Answering open questions later

When Dylan answers a question (here or through the catch-up skill):

1. `mcp_gbrain_get_page` with `include_content: true` to get the current page.
2. Apply the answer to the transcript, and turn its line into
   `- [x] <question> — <answer>`.
3. `mcp_gbrain_put_page` with the full updated content (it replaces the page).
4. When no `- [ ]` line remains, `mcp_gbrain_remove_tag` the `follow-up` tag.

Tell Dylan in one line what was filed, how many people or companies it links to,
and how many questions are still open.
