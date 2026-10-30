---
name: find-person
description: Use whenever a person comes up whom you need to identify or reach — "who is X?", "send Clayton a message that ...", "tell X that ...", a name with no page, a page with no role or LinkedIn link, or before asking Dylan who someone is. Runs the lookup chain (gbrain, Dylan's mail and calendar, HubSpot, LinkedIn, the web), asks Dylan only with a best guess, and picks the channel to reach them on. Replaces gbrain's resolve-before-asking.
---

# Find a person

Based on gbrain's `resolve-before-asking` skill (upstream
`resolve-before-asking@fc834ee`, gbrain 23fdb464), with Simple Salt's sources,
LinkedIn lookup and channel choice added. Use this one; the upstream copy is not
installed.

Two promises:

- **Never ask Dylan "who is X?" when the answer is already in gbrain or his
  records.** Asking is the last step, and it always carries a best guess.
- **Never message a guessed person.** If more than one person fits, or none
  clearly does, ask before sending anything.

## When this fires

- Dylan asks you to message, email, text, invite or tell someone something.
- Dylan asks who someone is, or what their role or relationship is.
- You are about to write "unknown", "who is X?" or a placeholder about a person.
- A person page has no role, no company or no `linkedin:` link and you need it.

## The lookup chain (in order; stop at the first clear answer)

Call one tool at a time, with every required argument.

### 1. gbrain: the person page

- `mcp__gbrain__search` with the name (and `"types": ["person"]` if offered).
  A first name alone: search it, then read every candidate page; do not stop at
  the top hit.
- `mcp__gbrain__get_page` on each candidate with `include_content: true`. Look
  at frontmatter (`company`, `role`, `linkedin`, `emails`, `phones`,
  `linkedin_degree`), the `## About` section and the timeline.
- `mcp__gbrain__get_timeline` and `mcp__gbrain__get_backlinks` for who and what
  references them (meetings, emails, companies, LinkedIn messages).
- If your tool list has `mcp__gbrain__entity` or `mcp__gbrain__synthesize`, use
  them for a quick card or a cross-page answer.

Three or more timeline entries with a consistent role, or a filled
`company`/`role`, is a clear answer.

### 2. gbrain: Dylan's mail and calendar

gbrain holds Dylan's Gmail (source `gmail`, `emails/...` pages) and calendar
(source `gcal`, `calendar/...` pages). Search both for the name:
`mcp__gbrain__query` / `mcp__gbrain__search` with `source_id` `gmail`, then
`gcal`. Recurring meetings or a long thread with one person usually settle who
"Clayton" is, with a surname and an email address. The email domain names the
company unless it is a personal domain (gmail.com and the like).

### 3. HubSpot (old CRM, lowest trust)

Load the `hubspot-lookup` skill, then search contacts by name, email or company.
Use it for surname, company, title, email and phone. gbrain, LinkedIn and Dylan's
own records win when they disagree. Do not copy HubSpot records into gbrain in
bulk; add only what you use about this one person.

### 4. LinkedIn profile URL

Needed to reach someone on LinkedIn, and worth filling for anyone Dylan knows.

- The person page's `linkedin:` field, if set.
- Otherwise web search with full name plus company (and city if known) and judge
  the match: name, company, title and location must agree.
- No clear match: tag the person page `needs-linkedin` (`mcp__gbrain__add_tag`;
  create the page first if there is none). A scheduled Sales
  Navigator lookup (run by Dylan's Claude with the CIC worker) picks up tagged
  pages every 30 minutes during the day, writes the link and clears the tag.
  Tell Dylan the lookup is pending; do not guess a profile.
- With a URL, `mcp__hermes_msg__linkedin_action` `visit` it: the visit lands the
  profile (degree, company, title) in gbrain within Dux Soup's daytime schedule.

### 5. Web search (other facts)

Only after 1–4: name plus every hint gathered so far. Write what you find back
to gbrain before using it (see "Write it back").

### 6. Ask Dylan (last resort), with a best guess

Only when the chain leaves more than one candidate or none:

```
Which Clayton do you mean?

1. Clayton Kohler — <role>, <company>; you meet him every other week (calendar)
2. Clayton <Surname> — <role>, <company>; emailed in March (Gmail)
3. Someone else — tell me who

Searched: gbrain people, your mail and calendar, HubSpot, LinkedIn.
```

Lead with your best guess, list at most three candidates with what places each
one, and stop the turn. Send nothing until Dylan picks.

## Reaching the person

Once you know who it is:

1. **Channel.** Use the channel Dylan named. If he named none, use the one he
   uses most with this person: look at the person's timeline and backlinks for
   the most recent two-way conversation (Gmail thread, LinkedIn message, text).
   No history: ask Dylan which channel, offering the ones you have an address for.
2. **Address.** Email: the address from their page, Gmail or HubSpot (load the
   `outbound-email` skill to send). Text: an E.164 phone number from their page
   or HubSpot. LinkedIn: the `linkedin.com/in/...` URL — never a name, email or
   phone; the tools refuse those.
3. **LinkedIn degree.** 1st-degree: `send_message` with `channel: "linkedin"`.
   Otherwise `linkedin_action` `connect` with the message as the note (300
   characters at most). Degree unknown: visit first and read it from
   `linkedin_search_results`. InMail only if Dylan asks.
4. **Report honestly.** LinkedIn messages and connects only queue; Dux Soup
   sends them during Dylan's schedule. Say "queued", not "sent", and name who it
   went to (full name, company) so Dylan can catch a wrong person.

## Write it back

gbrain is becoming the source of truth, so every lookup that finds something
new leaves it in gbrain:

- Person page `people/<first-last>`: `company`, `role`, `linkedin`, `emails`,
  `phones` in frontmatter, an `## About` line saying who they are and how Dylan
  knows them, and a `Source:` line. Follow AGENTS.md for companies, `works_at`
  links and `expected_revision`.
- Never leave placeholder text such as "Unknown relationship" or
  "[To be filled]". A page's first line should say who the person is.
- After you create or update several person pages at once (a meeting, an
  email thread, a list Dylan pasted), run steps 1–3 for each one you touched
  and fill its role, company and how Dylan knows them before you finish.

## Anti-patterns

- Messaging the top search hit for a first name without checking for others.
- Asking "who is X?" with no lookup, or without a best guess.
- Sending to a profile you guessed from a web search with a weak match.
- Using Hindsight recall as proof of who someone is; it is a hint. Check gbrain.
- Saying a LinkedIn message was sent when it was only queued.
