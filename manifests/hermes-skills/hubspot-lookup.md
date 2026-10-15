---
name: hubspot-lookup
description: Use when looking up a person, company, deal or past activity in Dylan's old HubSpot CRM. Gives the exact read-only tool calls and arguments for finding a contact by name or email and reading their companies, deals, notes, emails, calls and meetings.
---

# HubSpot lookup

HubSpot is Dylan's old CRM, read only. It is the lowest-trust source: gbrain,
LinkedIn and locally written info win when they disagree. Never copy HubSpot records
into gbrain in bulk.

Call the tools below directly, by these exact names, with JSON arguments shaped like
the examples. Every call needs `objectType`. Valid object types: `contacts`,
`companies`, `deals`, `notes`, `emails`, `calls`, `meetings`, `tasks`.

If a call returns an error, read it, fix the arguments, and try once more. Do not
repeat the same call: after three errors in a row the HubSpot tools are paused for a
minute and report themselves as unreachable, even though HubSpot itself is fine.

## Find a contact

By name, email, phone or company (free text across those fields):

`mcp_hubspot_hubspot_search_objects`
```json
{"objectType": "contacts", "query": "Jane Smith", "limit": 5,
 "properties": ["firstname", "lastname", "email", "company", "jobtitle", "phone", "lastmodifieddate"]}
```

By exact email:

```json
{"objectType": "contacts", "limit": 5,
 "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": "jane@example.com"}]}],
 "properties": ["firstname", "lastname", "email", "company", "jobtitle"]}
```

Filter `value` is always a string.
Operators: `EQ`, `NEQ`, `CONTAINS_TOKEN`, `GT`, `LT`, `HAS_PROPERTY`, `IN` (with `values`).

Companies work the same way with `"objectType": "companies"` and properties such as
`name`, `domain`, `industry`.

## Read what is linked to a record

1. List the linked IDs, one type at a time:

   `mcp_hubspot_hubspot_list_associations`
   ```json
   {"objectType": "contacts", "objectId": "101", "toObjectType": "deals"}
   ```
   Use `toObjectType` `companies`, `deals`, `notes`, `emails`, `calls` or `meetings`.

2. Read those records in one call:

   `mcp_hubspot_hubspot_batch_read_objects`
   ```json
   {"objectType": "deals", "inputs": [{"id": "2001"}, {"id": "2002"}],
    "properties": ["dealname", "amount", "dealstage", "closedate", "pipeline"]}
   ```

Useful properties by type:
- deals: `dealname`, `amount`, `dealstage`, `closedate`, `pipeline`
- notes: `hs_note_body`, `hs_timestamp`
- emails: `hs_email_subject`, `hs_email_text`, `hs_email_direction`, `hs_timestamp`
- calls: `hs_call_title`, `hs_call_body`, `hs_call_duration`, `hs_timestamp`
- meetings: `hs_meeting_title`, `hs_meeting_body`, `hs_meeting_start_time`

## Answering

Say the answer came from the old HubSpot CRM, and give the record's last-modified or
activity dates so Dylan can judge how stale it is. If nothing matches, say so and
suggest a different spelling or the email address; do not guess.
