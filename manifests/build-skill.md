---
name: build
description: "Delegate a build or coding task to Claude Code and report back the result."
license: MIT
metadata:
  author: Hermes Agent
  version: "6.2.0"
  tags: "Coding-Agent, Claude, Build, Deploy"
---

# Build — Delegate to Claude Code

Compose a prompt, dispatch to Claude Code, report back whatever it returns.

## 1. Auth check

```bash
terminal(command="[ -n \"$_HERMES_FORCE_CLAUDE_CODE_OAUTH_TOKEN\" ] && echo ok || echo missing", timeout=10)
```

Stop if output is `missing` — tell user `CLAUDE_CODE_OAUTH_TOKEN` secret is not mounted in the pod.

## 2. What — NO tool calls in this step

Do NOT run `terminal`, read files, query MCPs, or otherwise gather context here. Claude Code has the same terminal, memory, MCP, and internet access you do, and will gather what it needs far more efficiently than you can pre-chew it. Every command you run in this step is duplicated work that delays the build.

From the conversation history, create a summary of what Claude needs to build. If the intent is genuinely unclear, use clarify().

## 3. Dispatch

Claude Code builds exceed the 600s foreground cap, so use `background=true, notify_on_complete=true`. Background processes don't receive the injected `CLAUDE_CODE_OAUTH_TOKEN` — pass it explicitly from the container var:

```bash
terminal(
  command="CLAUDE_CODE_OAUTH_TOKEN=\"$_HERMES_FORCE_CLAUDE_CODE_OAUTH_TOKEN\" claude -p '<prompt>' --allowedTools 'Read,Write,Edit,Bash,Glob,Grep,WebFetch' --max-turns 50",
  workdir="/tmp",
  background=true,
  notify_on_complete=true
)
```

`<prompt>` content:

```
<intent>

Repo: <remote-url>  Branch: <branch>
Success criteria:
- <criterion>
```

Success criteria should be concrete and observable — not just an HTTP response code, but that the expected page or content actually appears (e.g. Flux kustomization shows Applied revision matching the commit, the app's login page loads with correct branding, pod Ready and serving real content).

Call `terminal()` exactly once. Do not poll — wait for the `notify_on_complete` notification, then go to step 4.

## 4. Report

When Claude finishes, tell the user:
- What was built or attempted
- Whether success criteria were met
- Errors, failures, or rate limits — verbatim from Claude's output

Report exactly one dispatch's outcome. Never re-dispatch — not to retry a failure, not to "improve" a result, not with a reworded prompt.
