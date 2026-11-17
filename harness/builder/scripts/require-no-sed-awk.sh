#!/usr/bin/env python3
"""PreToolUse hook — denies Bash tool calls that invoke `sed` or `awk` at a
command position (simplesalt/projects#305).

Why this exists: coding agents reach for `sed`/`awk` for quick text surgery
and routinely write careless expressions (unanchored substitutions, greedy
regexes, silent no-ops on a miss) that corrupt files in ways that are easy to
miss in review. The preferred toolchain for this sandbox is yq (YAML) -> jq
(JSON) -> jc (log/command output -> JSON) -> python3 (stdlib json/re/pathlib)
instead — each of those either understands the file's actual structure or
gives a real parser to work with, rather than a line-oriented text mangler.

This pod's image also now replaces the `sed`/`awk` binaries themselves with a
stub that exits 1 (see the Dockerfile change in the same initiative), so the
binaries won't work even if this hook is bypassed. This hook is therefore
defense-in-depth on top of that, not the sole mechanism: it exists to give a
clear, actionable denial message *before* the process even spawns, instead of
letting the agent hit an opaque "exit 1" from the stub and have to guess why.

Detection approach: match the raw Bash command string against a regex that
requires `sed`/`awk` to appear at a command-invocation position (start of
string, or immediately after `;`, `&`, `|`, or `(` — which also covers
`&&`/`||` since the lookbehind only needs the single trailing operator
character), optionally preceded by `busybox ` and optional whitespace. This
catches `sed -i ...`, `foo; awk '...'`, and `busybox sed -e ...`, but does not
flag `grep sed file.txt` or `echo "use sed here"`, where the literal word
"sed" appears but not as an invocation.

Known limitations (be honest, don't overclaim):
  - This is a best-effort regex over a raw shell string, not a real parse. It
    WILL miss:
      - a `busybox` invocation using unusual spacing/quoting the regex
        doesn't anticipate;
      - variable indirection (`s=sed; $s file`);
      - command substitution or eval of a base64-decoded or otherwise
        obfuscated string;
      - a wrapper script that itself calls sed/awk without the literal word
        appearing in the Bash tool_input;
      - `subprocess.run(["sed", ...])`-style invocation from inside an
        inline `python3 -c '...'` — the literal string "sed" there is just a
        Python list element, not shell command position, so this hook's
        regex (which only fires at shell command position) correctly does
        NOT flag it. That specific shape is an accepted gap, not a bug.
  - This is advisory, not a security boundary.
"""
import json
import re
import sys

TOOL_RE = re.compile(r'(?:^|(?<=[;&|(]))\s*(?:busybox\s+)?(?P<tool>sed|awk)\b')


def log(msg):
    print(f"require-no-sed-awk: {msg}", file=sys.stderr)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


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

    m = TOOL_RE.search(command)
    if not m:
        return

    tool = m.group("tool")
    via_busybox = "busybox" in m.group(0)
    via = " (via busybox)" if via_busybox else ""
    deny(
        f"`{tool}`{via} is disabled in this sandbox (simplesalt/projects#305) "
        "— line-oriented text mangling routinely corrupts files (unanchored "
        "substitutions, greedy regexes, silent no-ops on a miss) in ways "
        "easy to miss in review. Use the `Edit`/`Write` tools for file "
        "changes, or from Bash: yq (YAML) -> jq (JSON) -> jc (log/command "
        "output -> JSON) -> python3 (stdlib json/re/pathlib) — never regex "
        "line surgery on structured files. To feed YAML into python3, pipe "
        "it through `yq -o=json` first rather than installing PyYAML. If "
        f"this is a false match ('{tool}' appearing inside an unrelated "
        "string, not as an actual invocation), rephrase the command so the "
        "literal word doesn't sit at a command position."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"unexpected failure, allowing command unconfirmed (fail open): {exc}")
    sys.exit(0)
