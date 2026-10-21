Daily brief: email Dylan one short morning brief of today's meetings and who is waiting on him.

1. Today's meetings are listed above as JSON (from the script). For each meeting, for each address in `people`, find their person page (`mcp_gbrain_search` with the email, then the name) and take who they are and the last time Dylan met or emailed them (`mcp_gbrain_get_timeline`). Leave out conference sessions with no other attendees and reminders such as travel, unless they need preparation.
2. Waiting on Dylan: follow step 1 of the `catch-up` skill to gather open loops, skip ones that are not his to act on, and keep the 8 most urgent (who is waiting, how long, what is at stake).
3. Write the brief in light Markdown, plain words, no preamble:
   - `# Today` then, per meeting in time order: time, title, and one line per person (who they are, last contact, anything open with them).
   - `# Waiting on you` then one numbered line per loop: who, what, how long.
   - End with one line: "Reply or ask Hermes to work through these."
4. Send it with the `outbound-email` skill: from `dylan@simple-salt.com`, to `dylan.evans@simplesalt.company`, subject `Today: <weekday, month day>`.

Rules: read only, except sending this one email. Do not reply to anyone, close loops, or change pages. If there are no meetings and nothing waiting, send nothing and say so.
