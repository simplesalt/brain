#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/lib-effort.sh"
EFFORT_SCHEMA="$SCRIPT_DIR/effort_schema.py"

USAGE="usage: sync-deferred-parent.sh <child owner/repo#N | #N>"

case "${1:-}" in
  -h|--help) echo "$USAGE"; exit 0 ;;
esac

REF="${1:?$USAGE}"
resolve_ref "$REF"
CHILD="$REF_CANON"

BODY_TMP=$(mktemp)
trap 'rm -f "$BODY_TMP"' EXIT

ACTION="none"
EDGE="none"
GAP_ADDED=false
TASK=""
NOTE=""
PARENT=""
PARENT_STATUS_BEFORE=""
PARENT_STATUS_AFTER=""

summary() {
  jq -n \
    --arg child "$CHILD" \
    --arg parent "$PARENT" \
    --arg action "$ACTION" \
    --arg edge "$EDGE" \
    --argjson gap_added "$GAP_ADDED" \
    --arg task "$TASK" \
    --arg before "$PARENT_STATUS_BEFORE" \
    --arg after "$PARENT_STATUS_AFTER" \
    --arg note "$NOTE" \
    '{
      child: $child,
      parent: (if $parent == "" then null else $parent end),
      action: $action,
      edge: $edge,
      gap_added: $gap_added,
      task: (if $task == "" then null else $task end),
      parent_status_before: (if $before == "" then null else $before end),
      parent_status_after: (if $after == "" then null else $after end)
    } + (if $note == "" then {} else {note: $note} end)'
}

read_record() {
  local out
  if ! out=$("$SCRIPT_DIR/get-effort.sh" "$1"); then
    echo "error: could not read $1 — nothing was written" >&2
    return 1
  fi
  printf '%s' "$out"
}

load_parent() {
  PARENT_JSON=$(read_record "$PARENT")
  PARENT_BODY=$(jq -r '.body' <<<"$PARENT_JSON")
  PARENT_STATUS=$(jq -r '.status' <<<"$PARENT_JSON")
  PARENT_OPEN_BLOCKERS=$(jq -r '.open_blockers' <<<"$PARENT_JSON")
  PARENT_ITEM_ID=$(jq -r '.item_id' <<<"$PARENT_JSON")
  printf '%s' "$PARENT_BODY" > "$BODY_TMP"
  if ! grep -q '^### ' "$BODY_TMP"; then
    echo "error: refusing to update $PARENT — its body does not use the Effort schema headings (### Why, ### What, ...), so a rewritten body would overwrite a non-schema record; nothing was written — migrate its body first" >&2
    return 1
  fi
  local account_msg
  if ! account_msg=$(python3 "$EFFORT_SCHEMA" account --body-file "$BODY_TMP"); then
    {
      echo "error: refusing to update $PARENT — rebuilt body would silently drop content"
      echo "$account_msg"
      echo "nothing was written — fix $PARENT's body by hand first"
    } >&2
    return 1
  fi
}

patch_parent_body() {
  resolve_ref "$PARENT"
  gh api -X PATCH "repos/$REF_OWNER/$REF_REPO/issues/$REF_NUMBER" -f body="$1" >/dev/null
}

CHILD_JSON=$(read_record "$CHILD")
CHILD_STATUS=$(jq -r '.status' <<<"$CHILD_JSON")
CHILD_STATE=$(jq -r '.state' <<<"$CHILD_JSON")
CHILD_TITLE=$(jq -r '.title' <<<"$CHILD_JSON")
PARENT=$(jq -r '.parent' <<<"$CHILD_JSON")

if [ -z "$PARENT" ]; then
  NOTE="$CHILD has no parent Effort; nothing to sync"
  summary
  exit 0
fi

if [ "$CHILD_STATE" != "open" ]; then
  NOTE="$CHILD is $CHILD_STATE; a closed child changes nothing on its parent"
  summary
  exit 0
fi

load_parent
PARENT_STATUS_BEFORE="$PARENT_STATUS"
PARENT_STATUS_AFTER="$PARENT_STATUS"
EDGE_PRESENT=$(jq -r --arg c "$CHILD" '((.blockers // []) | index($c)) != null' <<<"$PARENT_JSON")

if [ "$CHILD_STATUS" = "Deferred" ]; then
  ACTION="defer"
  if [ "$EDGE_PRESENT" = "true" ]; then
    if ! "$SCRIPT_DIR/project-depend.sh" remove "$PARENT" --blocked-by "$CHILD" >&2; then
      echo "error: could not remove $PARENT's blocked-by edge to $CHILD — nothing else was written" >&2
      exit 1
    fi
    EDGE="removed"
    load_parent
  else
    EDGE="absent"
  fi

  RESULT=$(python3 "$EFFORT_SCHEMA" defer-parent --body-file "$BODY_TMP" --child "$CHILD" \
    --child-title "$CHILD_TITLE" --current "$PARENT_STATUS" --blockers "$PARENT_OPEN_BLOCKERS")
  GAP_ADDED=$(jq -r '.gap_added' <<<"$RESULT")
  TASK=$(jq -r '.task // ""' <<<"$RESULT")
  DERIVED=$(jq -r '.status' <<<"$RESULT")

  if [ "$GAP_ADDED" = "true" ] || [ -n "$TASK" ]; then
    patch_parent_body "$(jq -r '.body' <<<"$RESULT")"
  fi

  if [ "$DERIVED" != "${PARENT_STATUS:-Empty}" ]; then
    set_singleselect_field "$PARENT_ITEM_ID" "Status" "$DERIVED"
    PARENT_STATUS_AFTER="$DERIVED"
  fi
else
  ACTION="undefer"
  if [ "$EDGE_PRESENT" = "true" ]; then
    EDGE="present"
  else
    if ! "$SCRIPT_DIR/project-depend.sh" add "$PARENT" --blocked-by "$CHILD" >&2; then
      echo "error: could not record $PARENT as blocked by $CHILD — nothing else was written" >&2
      exit 1
    fi
    EDGE="added"
    load_parent
  fi

  RESULT=$(python3 "$EFFORT_SCHEMA" undefer-parent --body-file "$BODY_TMP" --child "$CHILD")
  TASK=$(jq -r '.task // ""' <<<"$RESULT")

  if [ -n "$TASK" ]; then
    patch_parent_body "$(jq -r '.body' <<<"$RESULT")"
  fi
  PARENT_STATUS_AFTER="$PARENT_STATUS"
fi

summary
