#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/lib-project.sh"

reconcile_status() {
  local ref="$1" out
  if out=$("$SCRIPT_DIR/update-effort.sh" "$ref" --reconcile 2>/dev/null); then
    local derived
    derived=$(jq -r '.status_derived // empty' <<<"$out" 2>/dev/null)
    [ -n "$derived" ] && echo "$ref status $derived"
  fi
  return 0
}

USAGE="usage: project-depend.sh {view <ref> | add <ref-X> --blocked-by <ref-Y> | remove <ref-X> --blocked-by <ref-Y>}"

SUBCOMMAND="${1:?$USAGE}"
shift || true

case "$SUBCOMMAND" in
  view)
    REF="${1:?$USAGE}"
    resolve_ref "$REF"
    ID=$(issue_node_id)
    RAW=$(gh api graphql -f query='
      query($id:ID!) {
        node(id:$id) {
          ... on Issue {
            blockedBy(first:50) { nodes { number title repository { nameWithOwner } } }
            blocking(first:50) { nodes { number title repository { nameWithOwner } } }
          }
        }
      }' -f id="$ID")
    echo "$REF_CANON is blocked by:"
    echo "$RAW" | jq -r '(.data.node.blockedBy.nodes // [])[] | "  - \(.repository.nameWithOwner)#\(.number): \(.title)"'
    BLOCKED_COUNT=$(echo "$RAW" | jq '(.data.node.blockedBy.nodes // []) | length')
    if [ "$BLOCKED_COUNT" -eq 0 ]; then echo "  (none)"; fi
    echo "$REF_CANON blocks:"
    echo "$RAW" | jq -r '(.data.node.blocking.nodes // [])[] | "  - \(.repository.nameWithOwner)#\(.number): \(.title)"'
    BLOCKING_COUNT=$(echo "$RAW" | jq '(.data.node.blocking.nodes // []) | length')
    if [ "$BLOCKING_COUNT" -eq 0 ]; then echo "  (none)"; fi
    ;;

  add)
    REF_X="${1:?$USAGE}"
    shift || true
    [ "${1:-}" = "--blocked-by" ] || { echo "error: $USAGE" >&2; exit 1; }
    REF_Y="${2:?$USAGE}"

    resolve_ref "$REF_X"; local_x_canon="$REF_CANON"; ID_X=$(issue_node_id)
    resolve_ref "$REF_Y"; local_y_canon="$REF_CANON"; ID_Y=$(issue_node_id)

    if [ "$ID_X" = "$ID_Y" ]; then
      echo "error: an issue cannot be blocked by itself ($local_x_canon)" >&2
      exit 1
    fi

    BLOCKER_STATUS=""
    BLOCKER_PARENT=""
    if BLOCKER_JSON=$("$SCRIPT_DIR/get-effort.sh" "$local_y_canon" 2>/dev/null); then
      BLOCKER_STATUS=$(jq -r '.status' <<<"$BLOCKER_JSON")
      BLOCKER_PARENT=$(jq -r '.parent' <<<"$BLOCKER_JSON")
    fi
    set +e
    REFUSAL=$(python3 "$SCRIPT_DIR/effort_schema.py" may-add-blocker --blocked "$local_x_canon" \
      --blocker-status "$BLOCKER_STATUS" --blocker-parent "$BLOCKER_PARENT" 2>&1)
    REFUSAL_RC=$?
    set -e
    if [ "$REFUSAL_RC" -ne 0 ]; then
      printf '%s\n' "$REFUSAL" >&2
      exit 1
    fi

    if ancestor_chain_contains "$ID_X" "$ID_Y"; then
      echo "error: refused — $local_y_canon is an ancestor of $local_x_canon; a parent never blocks its own child, the child blocks the parent (try: project-depend.sh add $local_y_canon --blocked-by $local_x_canon)" >&2
      exit 1
    fi

    if blocked_by_reaches "$ID_Y" "$ID_X"; then
      echo "error: refused — adding this edge would close a cycle ($local_y_canon is already transitively blocked by $local_x_canon)" >&2
      exit 1
    fi

    gh api graphql -f query='
      mutation($issueId:ID!, $blockingIssueId:ID!) {
        addBlockedBy(input:{issueId:$issueId, blockingIssueId:$blockingIssueId}) {
          clientMutationId
        }
      }' -f issueId="$ID_X" -f blockingIssueId="$ID_Y" >/dev/null

    echo "$local_x_canon is now blocked by $local_y_canon"
    reconcile_status "$local_x_canon"
    ;;

  remove)
    REF_X="${1:?$USAGE}"
    shift || true
    [ "${1:-}" = "--blocked-by" ] || { echo "error: $USAGE" >&2; exit 1; }
    REF_Y="${2:?$USAGE}"

    resolve_ref "$REF_X"; local_x_canon="$REF_CANON"; ID_X=$(issue_node_id)
    resolve_ref "$REF_Y"; local_y_canon="$REF_CANON"; ID_Y=$(issue_node_id)

    EXISTS=$(gh api graphql -f query='
      query($id:ID!) { node(id:$id) { ... on Issue { blockedBy(first:50) { nodes { id } } } } }' \
      -f id="$ID_X" \
      | jq --arg y "$ID_Y" '[(.data.node.blockedBy.nodes // [])[] | select(.id == $y)] | length')
    if [ "$EXISTS" -eq 0 ]; then
      echo "error: $local_x_canon is not currently blocked by $local_y_canon — nothing to remove" >&2
      exit 1
    fi

    gh api graphql -f query='
      mutation($issueId:ID!, $blockingIssueId:ID!) {
        removeBlockedBy(input:{issueId:$issueId, blockingIssueId:$blockingIssueId}) {
          clientMutationId
        }
      }' -f issueId="$ID_X" -f blockingIssueId="$ID_Y" >/dev/null

    echo "$local_x_canon is no longer blocked by $local_y_canon"
    reconcile_status "$local_x_canon"
    ;;

  *)
    echo "error: unknown subcommand '$SUBCOMMAND'" >&2
    echo "$USAGE" >&2
    exit 1
    ;;
esac
