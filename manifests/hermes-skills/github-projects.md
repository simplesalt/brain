---
name: github-projects
description: Use when finding, reading, creating or commenting on Simple Salt's org-wide project tasks (Efforts) in the simplesalt/projects GitHub repo. Run the gh_projects.py script with the terminal tool; the repo is built in.
---

# GitHub: simplesalt/projects

GitHub access is limited to one repo, `simplesalt/projects`. It holds Simple Salt's
org-wide project tasks ("Efforts") as GitHub issues. There is no code in it: the
`issues/` folder is a version-controlled JSON copy of each issue (`issues/760.json`).
You can read and write issues and their comments; the repo's files are read only.

## How to call it

Run this script with the terminal tool. The repo is built into it, so never pass an
owner or repo, and do not call the `mcp_github_*` tools directly.

```
python3 /opt/data/skills/simplesalt/github-projects/scripts/gh_projects.py <command> ...
```

| To | Command |
|---|---|
| Find issues by words | `search "hubspot export"` (add `--limit 20`) |
| List newest issues | `list` (add `--state open` or `--state closed`, `--limit 20`) |
| Read an issue | `read 760` |
| Read its comments | `comments 760` |
| Comment on an issue | `comment 760 "Comment text in Markdown"` |
| Create an issue | `create --title "[Feature] Short outcome-focused title" --body "..."` |
| Change an issue | `update 760 --state closed` (or `--title`, `--body`) |
| Read a repo file | `file issues/760.json` (no path lists the top folder) |

For long text, pass `-` and pipe it in: `printf '%s' "$TEXT" | ... comment 760 -`.
Output is the GitHub result as JSON. On failure the script prints `error: ...` and exits
1: read the error and fix the command, do not rerun it unchanged.

## Rules

- An Effort's body has fixed sections: Title, Assigned, Parent, Blockers, Why, What,
  Indicators, Gaps, Where, Facts, Unknowns, How, Tags. Status lives on the project
  board, not in the body.
- Comments are visible to everyone with repo access and notify watchers: comment only
  when asked, and say who it is from if you write for someone.
- Do not rewrite an Effort's body unless asked: coding agents keep it as their
  source of truth.
- Prefer `read` for current issue text; the JSON copy under `issues/` can lag.

## Answering

Link the issue you used: `https://github.com/simplesalt/projects/issues/<number>`.
