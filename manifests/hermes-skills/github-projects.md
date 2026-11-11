---
name: github-projects
description: Use when finding, reading or filing Simple Salt's org-wide project tasks (Efforts) in the simplesalt/projects GitHub repo, with the Effort tools mcp__efforts__effort_find, mcp__efforts__effort_get and mcp__efforts__effort_create.
---

# GitHub: simplesalt/projects

Simple Salt's org-wide project tasks ("Efforts") are GitHub issues in
`simplesalt/projects`. You reach them only through three Effort tools:

| To | Tool | Arguments |
|---|---|---|
| Find Efforts related to a need | `mcp__efforts__effort_find` | `why` (required), `what`, `where`, `limit` |
| Read one Effort | `mcp__efforts__effort_get` | `ref`: `simplesalt/projects#760` |
| File a new Effort | `mcp__efforts__effort_create` | see below |

## Filing an Effort

Before filing, `effort_find` with the same Why: if an open Effort already covers it, link
that one instead of filing a second.

`effort_create` takes:

- `title`: short and outcome-focused.
- `type`: `Feature` (something new), `Bug` (something broken), `Analysis` or `Advisory`.
- `assigned`: `ssint-build-brain` for Hermes, gbrain, Hindsight, Notion or cron work;
  ask Dylan when you cannot tell.
- `why`: the problem in Dylan's words, one or two sentences.
- `what`: the outcome when it is done.
- `indicators`: how anyone can check it is done, one per line starting with `- `.
- `where`: what you saw: the chat, the tool and its exact error, page slugs, dates.
- `tags`: optional, comma-separated.

Pass `dry_run: true` first when unsure; it shows the gate's checks without filing.
The tool answers with the new ref, or an error naming the field to fix: fix that field
and call again.

## What these tools do not do

There is no tool to comment on, edit, close or reassign an Effort. When Dylan asks for
one of those, tell him which Effort and what change, and that a coding agent or he makes it.

## Answering

Link the Effort you used: `https://github.com/simplesalt/projects/issues/<number>`.
