#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/lib-effort.sh"

case "${1:-}" in
  -h|--help) echo "usage: select-effort.sh <owner/repo#N>"; exit 0 ;;
esac

REF="${1:?usage: select-effort.sh <owner/repo#N>}"

resolve_ref "$REF"

if ITEM_ID=$(project_item_id); then
  ITEM_RC=0
else
  ITEM_RC=$?
fi
if [ "$ITEM_RC" -ne 0 ]; then
  if [ "$ITEM_RC" -eq 2 ]; then
    echo "error: could not query the SimpleSalt Delivery board for $REF_CANON (API call failed) — refusing to assume it is absent" >&2
  else
    echo "error: $REF_CANON is not on the SimpleSalt Delivery board (number $BOARD_NUMBER)" >&2
  fi
  exit 1
fi

HOLDER="$(hostname)/${CLAUDE_CODE_SESSION_ID:?CLAUDE_CODE_SESSION_ID not set}"
if ! CURRENT=$(field_text_value "$ITEM_ID" "In Use"); then
  echo "error: could not read $REF_CANON's lock holder (In Use field; API call failed) — refusing to select without positively confirming it is free" >&2
  exit 1
fi

if [ -n "$CURRENT" ]; then
  echo "error: $REF_CANON is already in use by $CURRENT" >&2
  exit 1
fi

set_text_field_value "$ITEM_ID" "In Use" "$HOLDER"

python3 -c '
import os, sys
path, ref = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        lines = [line.rstrip("\n") for line in f if line.strip()]
except FileNotFoundError:
    lines = []
lines = [line for line in lines if line != ref]
lines.append(ref)
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w") as f:
    for line in lines:
        f.write(line + "\n")
' "$HOME/.claude/selected-efforts.d/$CLAUDE_CODE_SESSION_ID" "$REF_CANON" || true

jq -n --arg ref "$REF_CANON" --arg holder "$HOLDER" '{ref: $ref, selected_by: $holder}'
