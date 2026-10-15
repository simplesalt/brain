---
name: notion-workspace
description: Use when finding, reading, editing or creating anything in Dylan's Notion — pages, databases and their rows. Explains that Notion databases are "data sources" and gives the exact tool calls for search, querying a database, reading a page and editing it.
---

# Notion

Only pages and databases shared with the Hermes integration are visible. Edits show
up in Notion as Hermes, and Notion keeps page history, so editing is safe. Call the
tools below directly, by these exact names.

If a call returns an error, read it, fix the arguments, and try once more. Do not
repeat the same call: after three errors in a row the Notion tools are paused for a
minute and report themselves as unreachable, even though Notion itself is fine.

## Databases are "data sources"

This Notion API treats a database's table as a **data source**, with its own ID.
- Search returns databases as objects with `"object": "data_source"`. Their `id` is a
  data source ID.
- Use a data source ID with `query-data-source` and `retrieve-a-data-source`.
- `retrieve-a-database` takes a **database** ID, not a data source ID, and only
  returns the list of data sources. Passing a data source ID fails with 400. You
  rarely need it; the database ID is in a data source's `parent.database_id`.
- A database link in a Notion URL holds the database ID. Call `retrieve-a-database`
  with it to get the data source ID, then query that.

## Find something

`mcp_notion_API_post_search` searches titles only:
```json
{"query": "Client onboarding", "page_size": 10}
```
Only databases: add `"filter": {"property": "object", "value": "data_source"}`.
Only pages: use `"value": "page"`.

## Read a database's rows

1. `mcp_notion_API_retrieve_a_data_source` with `{"data_source_id": "<id>"}` to see its
   property names and types.
2. `mcp_notion_API_query_data_source`:
   ```json
   {"data_source_id": "<id>", "page_size": 25,
    "filter": {"property": "Status", "status": {"equals": "In progress"}},
    "sorts": [{"property": "Due", "direction": "ascending"}]}
   ```
   The filter key matches the property type: `title`, `rich_text`, `select`,
   `status`, `multi_select`, `date`, `checkbox`, `number`, `people`, `relation`.
   Leave out `filter` to get every row. Page with `start_cursor` from `next_cursor`.

Each row is a page; its `id` works with the page tools below.

## Read a page

`mcp_notion_API_retrieve_page_markdown` with `{"page_id": "<id>"}` returns the whole
page as Markdown. Prefer it over walking blocks.
`mcp_notion_API_retrieve_a_page` returns the page's properties (for database rows).

## Edit a page

Targeted change (preferred):

`mcp_notion_API_update_page_markdown`
```json
{"page_id": "<id>", "type": "update_content",
 "update_content": {"content_updates": [{"old_str": "exact existing text", "new_str": "new text"}]}}
```
Read the page first so `old_str` matches exactly. To rewrite the whole page use
`"type": "replace_content"` with `"replace_content": {"new_str": "<full Markdown>"}`.

Change a row's properties with `mcp_notion_API_patch_page`:
```json
{"page_id": "<id>", "properties": {"Status": {"status": {"name": "Done"}}}}
```

## Create a page

Under a page: `mcp_notion_API_post_page` with
`{"parent": {"page_id": "<id>"}, "properties": {"title": [{"text": {"content": "Title"}}]}}`.
As a row in a database: `{"parent": {"database_id": "<database id>"}, "properties": {...}}`,
using the database's own property names. Add body text afterwards with
`update_page_markdown` (`replace_content`).

## Answering

Link the Notion page you used. If nothing matches, say so and ask whether the page
is shared with the Hermes integration; do not guess.
