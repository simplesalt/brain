#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/lib-effort.sh"

readonly PARENT_FIT_MIN_SCORE=0.50
readonly PARENT_FIT_MIN_MATCHED=3

USAGE="usage: create-effort.sh --title <text> --type <Feature|Bug|Analysis|Advisory> --assigned <bank> --why <text> --what <text> --indicators <text> --where <text> [--gaps <text>] [--facts <text>] [--unknowns <text>] [--how <text>] [--tags <text>] [--parent <ref> | --no-parent] [--blockers <ref[,ref...]>] [--status <Planning|Deferred>] [--dry-run]"

TITLE=""
TYPE=""
ASSIGNED=""
WHY=""
WHAT=""
INDICATORS=""
GAPS=""
WHERE=""
FACTS=""
UNKNOWNS=""
HOW=""
TAGS=""
PARENT_REF=""
NO_PARENT="false"
BLOCKERS=""
STATUS="Planning"
DRY_RUN="false"

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)    echo "$USAGE"; exit 0 ;;
    --title)      TITLE="${2:?--title requires a value}"; shift 2 ;;
    --type)       TYPE="${2:?--type requires a value}"; shift 2 ;;
    --assigned)   ASSIGNED="${2:?--assigned requires a value}"; shift 2 ;;
    --why)        WHY="${2:?--why requires a value}"; shift 2 ;;
    --what)       WHAT="${2:?--what requires a value}"; shift 2 ;;
    --indicators) INDICATORS="${2:?--indicators requires a value}"; shift 2 ;;
    --gaps)       GAPS="${2:?--gaps requires a value}"; shift 2 ;;
    --where)      WHERE="${2:?--where requires a value}"; shift 2 ;;
    --facts)      FACTS="${2:?--facts requires a value}"; shift 2 ;;
    --unknowns)   UNKNOWNS="${2:?--unknowns requires a value}"; shift 2 ;;
    --how)        HOW="${2:?--how requires a value}"; shift 2 ;;
    --tags)       TAGS="${2:?--tags requires a value}"; shift 2 ;;
    --parent)     PARENT_REF="${2:?--parent requires a value}"; shift 2 ;;
    --no-parent)  NO_PARENT="true"; shift ;;
    --blockers)   BLOCKERS="${2:?--blockers requires a value}"; shift 2 ;;
    --status)     STATUS="${2:?--status requires a value}"; shift 2 ;;
    --dry-run)    DRY_RUN="true"; shift ;;
    *) echo "error: unknown argument '$1'" >&2; echo "$USAGE" >&2; exit 1 ;;
  esac
done

for req in TITLE TYPE ASSIGNED WHY WHAT INDICATORS WHERE TAGS; do
  if [ -z "${!req}" ]; then
    echo "error: --$(echo "$req" | tr '[:upper:]' '[:lower:]') is required" >&2
    echo "$USAGE" >&2
    exit 1
  fi
done

if [ -n "$PARENT_REF" ] && [ "$NO_PARENT" = "true" ]; then
  echo "error: --parent and --no-parent are mutually exclusive" >&2
  echo "$USAGE" >&2
  exit 1
fi

case "$STATUS" in
  Planning|Deferred) ;;
  *) echo "error: --status must be one of: Planning Deferred (got '$STATUS')" >&2; exit 1 ;;
esac

validate_effort_type "$TYPE" || exit 1
validate_effort_assigned "$ASSIGNED" || exit 1

PARENT_CANON=""
PARENT_NODE_ID=""
FIT_FOUND="false"
FIT_SCORE=""
FIT_MATCHED=""

if [ -n "$PARENT_REF" ]; then
  resolve_ref "$PARENT_REF"
  PARENT_CANON="$REF_CANON"
  PARENT_NODE_ID=$(issue_node_id)
  if [ -z "$PARENT_NODE_ID" ]; then
    echo "error: parent '$PARENT_REF' does not exist or has no node id" >&2
    exit 1
  fi
elif [ "$NO_PARENT" = "true" ]; then
  :
else
  QUERY_TMP=$(mktemp)
  if ! effort_search_query "$WHY" "$WHAT" "$WHERE" > "$QUERY_TMP"; then
    rm -f "$QUERY_TMP"
    exit 1
  fi
  QUERY=$(cat "$QUERY_TMP")
  rm -f "$QUERY_TMP"
  TOKENS="$EFFORT_QUERY_TOKENS"
  CANDIDATES=$(search_efforts "$QUERY" 5)

  BEST_NUMBER=""
  BEST_MATCHED=-1
  BEST_SCORE="0.00"
  BEST_INDEX=-1
  INDEX=0
  while IFS= read -r item; do
    [ -z "$item" ] && continue
    c_number=$(jq -r '.number' <<<"$item")
    c_title=$(jq -r '.title' <<<"$item")
    c_body=$(jq -r '.body // ""' <<<"$item")
    read -r c_matched c_score < <(effort_relevance "$c_title" "$c_body" "$TOKENS")
    BETTER=$(python3 -c "
import sys
score, matched, best_score, best_matched, best_index = float(\"$c_score\"), int(\"$c_matched\"), float(\"$BEST_SCORE\"), int(\"$BEST_MATCHED\"), int(\"$BEST_INDEX\")
if best_index < 0:
    print(1)
elif score > best_score:
    print(1)
elif score == best_score and matched > best_matched:
    print(1)
else:
    print(0)
")
    if [ "$BETTER" = "1" ]; then
      BEST_NUMBER="$c_number"
      BEST_MATCHED="$c_matched"
      BEST_SCORE="$c_score"
      BEST_INDEX="$INDEX"
    fi
    INDEX=$((INDEX + 1))
  done < <(echo "$CANDIDATES" | jq -c '.[]')

  if [ -n "$BEST_NUMBER" ]; then
    FIT_SCORE="$BEST_SCORE"
    FIT_MATCHED="$BEST_MATCHED"
    CLEARS_FLOOR=$(python3 -c "
score, matched = float(\"$BEST_SCORE\"), int(\"$BEST_MATCHED\")
print(1 if (score >= $PARENT_FIT_MIN_SCORE and matched >= $PARENT_FIT_MIN_MATCHED) else 0)
")
    if [ "$CLEARS_FLOOR" = "1" ]; then
      resolve_ref "$DEFAULT_REPO#$BEST_NUMBER"
      PARENT_CANON="$REF_CANON"
      PARENT_NODE_ID=$(issue_node_id)
      FIT_FOUND="true"
    else
      echo "note: no existing Effort is related enough to be the parent (best candidate $DEFAULT_REPO#$BEST_NUMBER scored $BEST_SCORE); created top-level" >&2
    fi
  else
    echo "note: no existing Effort is related enough to be the parent; created top-level" >&2
  fi
fi

BODY=$(effort_body "$TITLE" "$ASSIGNED" "${PARENT_CANON:-}" "$BLOCKERS" "$WHY" "$WHAT" \
  "$INDICATORS" "$GAPS" "$WHERE" "$FACTS" "$UNKNOWNS" "$HOW" "$TAGS")

GATE_TMP=$(mktemp)
printf '%s' "$BODY" > "$GATE_TMP"
if ! GATE_OUT=$(python3 "$SCRIPT_DIR/effort_schema.py" gate --body-file "$GATE_TMP" --from Empty --to Planning 2>&1); then
  rm -f "$GATE_TMP"
  {
    printf '%s\n' "$GATE_OUT"
    echo "nothing was created"
  } >&2
  exit 1
fi
rm -f "$GATE_TMP"

if [ "$DRY_RUN" = "true" ]; then
  jq -n \
    --arg parent "${PARENT_CANON:-}" \
    --argjson fit "$FIT_FOUND" \
    --argjson score "${FIT_SCORE:-null}" \
    --argjson matched "${FIT_MATCHED:-null}" \
    '{ref: null, url: null, parent: (if $parent == "" then null else $parent end), parent_via_fit_finding: $fit, parent_fit_score: $score, parent_fit_matched: $matched}'
  exit 0
fi

REPO_OWNER="${DEFAULT_REPO%%/*}"
REPO_NAME="${DEFAULT_REPO#*/}"
REPO_NODE_ID=$(gh api "repos/$REPO_OWNER/$REPO_NAME" --jq .node_id)

TYPE_ID=$(resolve_issue_type_id "$TYPE")
if [ -z "$TYPE_ID" ]; then
  echo "error: no issue type named '$TYPE' in org $ORG" >&2
  exit 1
fi

PROJECT_ID=$(project_id)

GH_TITLE="[$TYPE] $TITLE"

INPUT=$(jq -n \
  --arg repo "$REPO_NODE_ID" \
  --arg title "$GH_TITLE" \
  --arg body "$BODY" \
  --arg typeId "$TYPE_ID" \
  --arg projectId "$PROJECT_ID" \
  --arg parentId "$PARENT_NODE_ID" \
  '{
    repositoryId: $repo,
    title: $title,
    body: $body,
    issueTypeId: $typeId,
    projectV2Ids: [$projectId]
  } + (if $parentId != "" then {parentIssueId: $parentId} else {} end)')

RAW=$(jq -n --argjson input "$INPUT" '{
    query: "mutation($input: CreateIssueInput!) { createIssue(input: $input) { issue { id number url title } } }",
    variables: { input: $input }
  }' | gh api graphql --input - 2>&1) || {
  echo "error: createIssue mutation failed: $RAW" >&2
  exit 1
}

NEW_NUMBER=$(echo "$RAW" | jq -r '.data.createIssue.issue.number // empty')
NEW_URL=$(echo "$RAW" | jq -r '.data.createIssue.issue.url // empty')
NEW_ID=$(echo "$RAW" | jq -r '.data.createIssue.issue.id // empty')
if [ -z "$NEW_NUMBER" ] || [ -z "$NEW_ID" ]; then
  echo "error: createIssue did not return a new issue: $RAW" >&2
  exit 1
fi
NEW_REF="$REPO_OWNER/$REPO_NAME#$NEW_NUMBER"

ITEM_ID=""
for attempt in $(seq 1 10); do
  resolve_ref "$NEW_REF"
  if ITEM_ID=$(project_item_id); then
    break
  fi
  ITEM_ID=""
  sleep 2
done

if [ -n "$ITEM_ID" ]; then
  set_singleselect_field "$ITEM_ID" "Assigned" "$ASSIGNED"
  set_singleselect_field "$ITEM_ID" "Status" "$STATUS"
else
  echo "warning: $NEW_REF was created but has not yet appeared on the SimpleSalt Delivery board — Assigned and Status were not set, so it will read as statusless; re-check with 'gh issue view $NEW_REF'" >&2
fi

if [ -n "$BLOCKERS" ]; then
  IFS=',' read -ra BLOCKER_REFS <<< "$BLOCKERS"
  for b in "${BLOCKER_REFS[@]}"; do
    b="${b# }"
    b="${b% }"
    [ -z "$b" ] && continue
    "$SCRIPT_DIR/project-depend.sh" add "$NEW_REF" --blocked-by "$b" >&2 || \
      echo "warning: could not record blocker '$b' on $NEW_REF" >&2
  done
fi

if [ -n "$PARENT_CANON" ] && [ "$STATUS" != "Deferred" ]; then
  "$SCRIPT_DIR/project-depend.sh" add "$PARENT_CANON" --blocked-by "$NEW_REF" >&2 || \
    echo "warning: could not record $NEW_REF as a blocker of its parent $PARENT_CANON" >&2
fi

jq -n \
  --arg ref "$NEW_REF" \
  --arg url "$NEW_URL" \
  --arg parent "${PARENT_CANON:-}" \
  --argjson fit "$FIT_FOUND" \
  --argjson score "${FIT_SCORE:-null}" \
  --argjson matched "${FIT_MATCHED:-null}" \
  '{ref: $ref, url: $url, parent: (if $parent == "" then null else $parent end), parent_via_fit_finding: $fit, parent_fit_score: $score, parent_fit_matched: $matched}'
