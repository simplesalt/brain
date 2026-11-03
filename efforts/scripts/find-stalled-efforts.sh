#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/lib-effort.sh"

USAGE="usage: find-stalled-efforts.sh [--condition <empty-status|paused|unheld-doing|clear-blocked|deferred>] [--limit N]"

CONDITION=""
LIMIT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --condition) CONDITION="${2:?--condition requires a value}"; shift 2 ;;
    --limit) LIMIT="${2:?--limit requires a value}"; shift 2 ;;
    *) echo "error: unknown argument '$1'" >&2; echo "$USAGE" >&2; exit 1 ;;
  esac
done

case "$CONDITION" in
  ""|empty-status|paused|unheld-doing|clear-blocked|deferred) ;;
  *)
    echo "error: --condition must be one of: empty-status paused unheld-doing clear-blocked deferred (got '$CONDITION')" >&2
    exit 1
    ;;
esac

FIELD_NAMES=$(gh api graphql -f query='
  query($org:String!, $number:Int!) {
    organization(login:$org) {
      projectV2(number:$number) {
        fields(first:50) { nodes { ... on ProjectV2FieldCommon { name } } }
      }
    }
  }' -f org="$ORG" -F number="$BOARD_NUMBER" \
  --jq '[.data.organization.projectV2.fields.nodes[] | .name? // empty]')

for required in Status Assigned "In Use"; do
  if ! jq -e --arg n "$required" 'index($n) != null' <<<"$FIELD_NAMES" >/dev/null; then
    echo "error: board $BOARD_NUMBER has no field named '$required'; refusing to report, every Effort would read as stalled" >&2
    exit 1
  fi
done

TMP_RAW=$(mktemp)
TMP_NORM=$(mktemp)
TMP_BLOCKED=$(mktemp)
TMP_CLEAR=$(mktemp)
trap 'rm -f "$TMP_RAW" "$TMP_NORM" "$TMP_BLOCKED" "$TMP_CLEAR"' EXIT

: > "$TMP_RAW"

CURSOR="null"
while :; do
  PAGE=$(gh api graphql -f query='
    query($org:String!, $number:Int!, $cursor:String) {
      organization(login:$org) {
        projectV2(number:$number) {
          items(first:100, after:$cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              content {
                __typename
                ... on Issue {
                  id
                  number
                  title
                  url
                  state
                  repository { nameWithOwner }
                }
              }
              status: fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
              assigned: fieldValueByName(name:"Assigned") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
              inuse: fieldValueByName(name:"In Use") { ... on ProjectV2ItemFieldTextValue { text } }
            }
          }
        }
      }
    }' -f org="$ORG" -F number="$BOARD_NUMBER" -F cursor="$CURSOR")

  echo "$PAGE" | jq -c '.data.organization.projectV2.items.nodes[]' >> "$TMP_RAW"

  HAS_NEXT=$(echo "$PAGE" | jq -r '.data.organization.projectV2.items.pageInfo.hasNextPage')
  CURSOR=$(echo "$PAGE" | jq -r '.data.organization.projectV2.items.pageInfo.endCursor')
  [ "$HAS_NEXT" = "true" ] || break
done

jq -c '
  select(.content.__typename == "Issue" and .content.state == "OPEN")
  | {
      ref: (.content.repository.nameWithOwner + "#" + (.content.number | tostring)),
      number: .content.number,
      title: .content.title,
      url: .content.url,
      status: (.status.name // ""),
      assigned: (.assigned.name // ""),
      in_use: (.inuse.text // ""),
      node_id: .content.id
    }
' "$TMP_RAW" > "$TMP_NORM"

: > "$TMP_CLEAR"

if [ -z "$CONDITION" ] || [ "$CONDITION" = "clear-blocked" ]; then
  jq -c 'select(.status == "Blocked")' "$TMP_NORM" > "$TMP_BLOCKED"
  mapfile -t BLOCKED_LINES < "$TMP_BLOCKED"
  TOTAL_BLOCKED=${#BLOCKED_LINES[@]}
  BATCH_SIZE=30
  i=0
  while [ "$i" -lt "$TOTAL_BLOCKED" ]; do
    VAR_DECLS=""
    QUERY_PARTS=""
    ARGS=()
    idx=0
    j=$i
    while [ "$j" -lt "$TOTAL_BLOCKED" ] && [ "$idx" -lt "$BATCH_SIZE" ]; do
      NODE_ID=$(jq -r '.node_id' <<<"${BLOCKED_LINES[$j]}")
      VAR_DECLS+="\$id$idx:ID!,"
      QUERY_PARTS+="i$idx: node(id:\$id$idx) { ... on Issue { blockedBy(first:50) { nodes { state } } } } "
      ARGS+=(-f "id$idx=$NODE_ID")
      idx=$((idx + 1))
      j=$((j + 1))
    done
    VAR_DECLS="${VAR_DECLS%,}"
    QUERY="query($VAR_DECLS) { $QUERY_PARTS }"
    RESULT=$(gh api graphql -f query="$QUERY" "${ARGS[@]}")
    idx=0
    k=$i
    while [ "$k" -lt "$j" ]; do
      ALL_CLOSED=$(jq -r --arg alias "i$idx" '(.data[$alias].blockedBy.nodes // []) | all(.state == "CLOSED")' <<<"$RESULT")
      if [ "$ALL_CLOSED" = "true" ]; then
        jq -r '.ref' <<<"${BLOCKED_LINES[$k]}" >> "$TMP_CLEAR"
      fi
      idx=$((idx + 1))
      k=$((k + 1))
    done
    i=$j
  done
fi

CLEAR_REFS_JSON=$(jq -R -s -c 'split("\n") | map(select(length > 0))' "$TMP_CLEAR")
LIMIT_JSON="${LIMIT:-null}"

if [ -z "$CONDITION" ]; then
  DEFERRED_COUNT=$(jq -s '[.[] | select(.status == "Deferred")] | length' "$TMP_NORM")
  if [ "$DEFERRED_COUNT" -gt 0 ]; then
    echo "deferred: $DEFERRED_COUNT (not stalled, excluded from this output; list with --condition deferred)" >&2
  fi
fi

jq -s -c \
  --argjson clear "$CLEAR_REFS_JSON" \
  --arg condition "$CONDITION" \
  --argjson limit "$LIMIT_JSON" '
  def rank:
    if .condition == "empty-status" then 0
    elif .condition == "paused" then 1
    elif .condition == "unheld-doing" then 2
    else 3 end;
  ($clear | map({(.): true}) | add // {}) as $clearset
  | map(
      if .status == "" then . + {condition: "empty-status"}
      elif .status == "Paused" then . + {condition: "paused"}
      elif .status == "Doing" and .in_use == "" then . + {condition: "unheld-doing"}
      elif .status == "Blocked" and ($clearset[.ref] // false) then . + {condition: "clear-blocked"}
      elif .status == "Deferred" then . + {condition: "deferred"}
      else empty end
    )
  | (if $condition == "" then map(select(.condition != "deferred")) else map(select(.condition == $condition)) end)
  | sort_by([rank, .number])
  | (if $limit == null then . else .[0:$limit] end)
  | map({ref, number, title, url, status, assigned, in_use, condition})
' "$TMP_NORM"
