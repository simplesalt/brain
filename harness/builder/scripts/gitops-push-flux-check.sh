#!/usr/bin/env python3
"""PostToolUse hook — after a `git push origin` (or a bare `git push`, since
this pod's only configured remote is origin) in a repo flagged as GitOps via
$GITOPS=TRUE, waits for Flux's reconcile interval to plausibly complete, then
wakes Claude with a system reminder to check reconciliation status via the
read-only flux MCP tools.

$GITOPS is set per-repo via that repo's own .claude/settings.json "env"
block — Claude Code's built-in per-project env loading — not by anything
this pod sources itself. Recognized values are TRUE, FALSE, and unset/blank;
only an exact "TRUE" triggers this hook, so a repo that sets FALSE or never
sets it at all never does.

Registered with asyncRewake: true (see simplesalt.json), so the wait runs in
the background instead of blocking the session, and statusMessage carries
the immediate "waiting" notice shown while this process runs. asyncRewake
only wakes Claude on exit code 2, with stderr (or stdout if stderr is empty)
shown as the reminder text — so every "nothing to do here" path below exits
0 quietly, and only the path that should speak up exits 2 after sleeping.

Detection is a regex over the raw Bash command string (advisory, not
sound), not a real shell parse — chained commands, unusual quoting, or a
wrapper script that itself runs `git push` can all evade it. A push that
plainly failed (common git rejection phrasing found in tool_response) is
skipped on a best-effort basis; PostToolUse's
tool_response shape for Bash isn't guaranteed to carry a clean exit code, so
this looks for failure text rather than trusting a field that may not exist.

Fail-open, always: any parse error or unexpected exception exits 0 (silent)
— a bug in this hook must never wake Claude with fabricated urgency, and
(being async) can never block a push either.
"""
import json
import os
import re
import subprocess
import sys
import time

PUSH_RE = re.compile(r"\bgit\s+(?:[\w.:/@=-]+\s+)*push\b(?P<rest>[^&;|]*)")
FAILURE_HINTS = (
    "rejected", "non-fast-forward", "failed to push", "fatal:",
    "could not read from remote repository", "permission denied",
)
WAIT_SECONDS = 120


def log(msg):
    print(f"gitops-push-flux-check: {msg}", file=sys.stderr)


def response_text(tool_response):
    if isinstance(tool_response, str):
        return tool_response
    if isinstance(tool_response, dict):
        parts = [
            v for k in ("text", "stdout", "stderr", "output")
            if isinstance((v := tool_response.get(k)), str)
        ]
        return "\n".join(parts)
    return ""


def looks_failed(text):
    lowered = text.lower()
    return any(hint in lowered for hint in FAILURE_HINTS)


def short_sha(cwd):
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=8,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    if data.get("tool_name") != "Bash":
        return

    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not command:
        return

    match = PUSH_RE.search(command)
    if not match:
        return

    tokens = [t for t in match.group("rest").split() if not t.startswith("-")]
    if tokens and tokens[0] != "origin":
        return

    if os.environ.get("GITOPS") != "TRUE":
        return

    if looks_failed(response_text(data.get("tool_response"))):
        log("push looks like it failed, skipping flux check")
        return

    cwd = data.get("cwd") or os.getcwd()
    sha = short_sha(cwd)

    time.sleep(WAIT_SECONDS)

    where = f" ({sha})" if sha else ""
    print(
        f"git push to a gitops-flagged repo{where} completed roughly "
        f"{WAIT_SECONDS // 60} minutes ago. Check Flux reconciliation status "
        "for the pushed commit using the read-only flux MCP tools (e.g. "
        "mcp__flux__get_kubernetes_resources for the relevant "
        "Kustomization/HelmRelease, or mcp__flux__get_kubernetes_logs if "
        "something looks stuck) and report what reconciled or didn't.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"unexpected failure, staying silent (fail open): {exc}")
    sys.exit(0)
