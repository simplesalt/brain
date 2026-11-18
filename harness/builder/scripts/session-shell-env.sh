#!/bin/sh
cat >/dev/null
[ -n "${CLAUDE_ENV_FILE:-}" ] || exit 0
line=". $(cd "$(dirname "$0")" && pwd)/builder-shell.sh"
grep -qxF "${line}" "${CLAUDE_ENV_FILE}" 2>/dev/null || printf '%s\n' "${line}" >> "${CLAUDE_ENV_FILE}"
exit 0
