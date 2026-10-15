---
name: github-projects
description: Use when finding, reading, creating or commenting on Simple Salt's org-wide project tasks (Efforts) in the simplesalt/projects GitHub repo. Gives the exact tool calls and arguments.
---

# GitHub: simplesalt/projects

GitHub access is limited to one repo, `simplesalt/projects`. It holds Simple Salt's
org-wide project tasks ("Efforts") as GitHub issues. There is no code in it: the
`issues/` folder is a version-controlled JSON copy of each issue (`issues/760.json`).
You can read and write issues and their comments; the repo's files are read only.

Every call needs `"owner": "simplesalt", "repo": "projects"`. Call the tools below directly, by
these exact names.

If a call returns an error, read it, fix the arguments, and try once more. Do not
repeat the same call: after three errors in a row the GitHub tools are paused for a
minute and report themselves as unreachable, even though GitHub itself is fine.

## Find issues

By words:

`mcp_github_search_issues`
```json
{"owner": "simplesalt", "repo": "projects", "query": "hubspot export", "perPage": 10}
```
`query` is plain words; give alternative wordings as extra words, not with OR.

Newest or by state:

`mcp_github_list_issues`
```json
{"owner": "simplesalt", "repo": "projects", "state": "OPEN", "perPage": 10}
```
`state` is `OPEN` or `CLOSED`. Page with `after` from the previous result.

## Read an issue

`mcp_github_issue_read`
```json
{"owner": "simplesalt", "repo": "projects", "issue_number": 760, "method": "get"}
```
`method` is `get` (the issue), `get_comments`, `get_sub_issues`, `get_parent` or
`get_labels`.

An Effort's body has fixed sections: Title, Assigned, Parent, Blockers, Why, What,
Indicators, Gaps, Where, Facts, Unknowns, How, Tags. Status lives on the project
board, not in the body.

## Comment

`mcp_github_add_issue_comment`
```json
{"owner": "simplesalt", "repo": "projects", "issue_number": 760, "body": "Comment text in Markdown."}
```
Comments are public to everyone with repo access and notify watchers: comment only
when asked, and say who it is from if you are writing for someone.

## Create or update an issue

`mcp_github_issue_write`
```json
{"owner": "simplesalt", "repo": "projects", "method": "create",
 "title": "[Feature] Short outcome-focused title", "body": "..."}
```
To change an existing issue use `"method": "update"` with `issue_number` and only the
fields to change (`title`, `body`, `state`: `open` or `closed`, `labels`). Do not
rewrite an Effort's body unless asked: coding agents keep it as their source of truth.

## Files

`mcp_github_get_file_contents` with `{"owner": "simplesalt", "repo": "projects", "path": "issues/760.json"}`
reads the JSON copy of an issue; `"path": "/"` lists the top folder. Prefer
`issue_read` for current issue text: the JSON copy can lag.

## Answering

Link the issue (`https://github.com/simplesalt/projects/issues/<number>`) you used.
