---
name: note-capture
description: Use when Dylan sends notes to get into gbrain, usually a photo of handwritten notes (notebook page, whiteboard, sticky note). Transcribes them, works out what they were for and who is who, turns every action item into a tracked item with an owner, asks about anything missing instead of guessing or dropping it, and files the note so nothing unresolved is forgotten.
---

# Note capture

The point is that nothing on the page is lost or silently guessed. Every action
item ends up tracked with an owner, and everything you cannot settle is either
asked now or recorded as an open question that the `catch-up` skill brings back
until Dylan answers it.

## 1. Transcribe

Read the photo the same way you read business cards. Transcribe faithfully: keep
structure (headings, bullets, arrows as "→", boxes, underlines as emphasis),
keep abbreviations and initials as written, do not tidy the wording. Mark any
word you cannot read as `[?]` with your best guess, e.g. `[?Kessler]`.

## 2. Work out the context yourself first

Before asking anything, settle what a check can settle:

- What the note was for: if it has a date, or Dylan says when, look for a
  meeting then: `mcp_gbrain_search` with `{"query": "<YYYY-MM-DD> <names on the
  page>", "source_id": "gcal", "types": ["meeting"]}` (meeting slugs start with
  `calendar/YYYY/MM/YYYY-MM-DD-`). A matching meeting tells you who was there.
- Who is who: for every name, initial or company, `mcp_gbrain_search` (add the
  company or meeting attendees as context). Treat a match as settled only when
  it is unambiguous; otherwise it stays a question.
- Where it belongs: the company, project or person the note is mostly about.

## 3. Lay out what you understood and what is missing

Write Dylan one message with three parts:

1. **What the note says**: the transcript, then a short reading of it: what it
   was for (meeting with whom, when), the decisions, and each action item as
   "owner → task (due)".
2. **What you settled**: who is who and the meeting match, each with what it is
   based on, so he can correct a wrong match.
3. **What is missing**, as numbered questions, most important first, each
   answerable in a word or a line, with your best guess where you have one:
   - illegible words and numbers;
   - what the note was for, if no meeting matched;
   - who each unmatched name or initial is;
   - for each action item without one: who owns it, and when it is due;
   - where it should be filed, if it is not clearly about one company or project.

Ask in the style of a clarifying conversation: reflect back ("It sounds like
this was your call with Acme on Tuesday — is that right?"), keep each question
easy to answer, and move to the next open one as each is settled. Do not ask
about anything step 2 already settled unless it is ambiguous.

If Dylan answers some and not others, or says "later", stop asking and file with
everything still open recorded in step 4. Never drop an unanswered question and
never fill it with a guess.

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
meeting: <calendar page slug, if matched>
about: <company/project/person slug it is filed under, if known>
tags: [handwritten]
---

# <short topic>

<what it was for, one line>

<corrected transcript>

## Action items

- [ ] <owner> → <task> (due <date or "no date">)

## Open questions

- [ ] <question>
```

Every action item goes under `## Action items`, including ones owned by someone
other than Dylan (he is tracking that they happen). An item whose owner is
unknown is written with owner `?` and also gets an open question asking who owns
it. Leave out a section only when it is empty.

When the page has any unchecked item in either section, also call
`mcp_gbrain_add_tag` with tag `follow-up`; that is what makes `catch-up` bring it
back.

Then link each settled person, company, project or meeting:
`mcp_gbrain_add_link` from the note's slug to the entity's slug, with `context`
naming what the note says about them. For each action item owned by or owed to a
known person, also `mcp_gbrain_add_timeline_entry` on that person's page
(`{"slug": <person>, "date": <note date>, "summary": "<owner> → <task> (due ...)",
"source": <note slug>}`), so it shows up in their history. Never create a new person or
company page from a note alone; an unmatched name stays an open question.

Close with one line: what was filed and where, how many action items it tracks,
and how many questions are still open.

## 5. Later: answering questions and closing items

When Dylan answers a question or reports an action item done (here or through
`catch-up`):

1. `mcp_gbrain_get_page` with `include_content: true` to get the current page.
2. Apply the answer: fix the transcript, fill the owner or due date, add the
   links it makes possible (step 4), and turn the line into
   `- [x] <question> — <answer>` or `- [x] <owner> → <task> — done <date>`.
   An answer can raise a new question; add it as a new `- [ ]` line.
3. `mcp_gbrain_put_page` with the full updated content (it replaces the page).
4. When no `- [ ]` line remains in either section, `mcp_gbrain_remove_tag` the
   `follow-up` tag.
