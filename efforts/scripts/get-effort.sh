#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/lib-effort.sh"

case "${1:-}" in
  -h|--help) echo "usage: get-effort.sh <owner/repo#N | #N>"; exit 0 ;;
esac

REF="${1:?usage: get-effort.sh <owner/repo#N | #N>}"

resolve_ref "$REF"

set +e
RAW=$(gh api graphql -f query='
  query($owner:String!, $repo:String!, $number:Int!) {
    repository(owner:$owner, name:$repo) {
      issue(number:$number) {
        id
        title
        state
        body
        parent { repository { nameWithOwner } number }
        blockedBy(first:50) { nodes { repository { nameWithOwner } number state } }
        subIssues(first:100) {
          nodes {
            repository { nameWithOwner }
            number
            title
            state
            projectItems(first:10) {
              nodes {
                project { number }
                fieldValues(first:20) {
                  nodes {
                    ... on ProjectV2ItemFieldSingleSelectValue {
                      name
                      field { ... on ProjectV2FieldCommon { name } }
                    }
                    ... on ProjectV2ItemFieldNumberValue {
                      number
                      field { ... on ProjectV2FieldCommon { name } }
                    }
                  }
                }
              }
            }
          }
        }
        projectItems(first:20) {
          nodes {
            id
            project { number }
            fieldValues(first:20) {
              nodes {
                ... on ProjectV2ItemFieldSingleSelectValue {
                  name
                  field { ... on ProjectV2FieldCommon { name } }
                }
                ... on ProjectV2ItemFieldTextValue {
                  text
                  field { ... on ProjectV2FieldCommon { name } }
                }
              }
            }
          }
        }
      }
    }
  }' -f owner="$REF_OWNER" -f repo="$REF_REPO" -F number="$REF_NUMBER" 2>/dev/null)
set -e

if [ -z "$RAW" ]; then
  echo "error: could not query the SimpleSalt Delivery board for $REF_CANON (API call failed) — refusing to assume it is absent" >&2
  exit 1
fi

ISSUE=$(echo "$RAW" | jq -c '.data.repository.issue // empty' 2>/dev/null)
if [ -z "$ISSUE" ]; then
  echo "error: $REF_CANON could not be fetched" >&2
  exit 1
fi

TITLE=$(echo "$ISSUE" | jq -r '.title // empty')
STATE=$(echo "$ISSUE" | jq -r '(.state // "") | ascii_downcase')
BODY=$(echo "$ISSUE" | jq -r '.body // empty')

ITEM=$(echo "$ISSUE" | jq -c --argjson n "$BOARD_NUMBER" '
  [(.projectItems.nodes // [])[] | select(.project.number == $n)] | first // empty
' 2>/dev/null)

if [ -z "$ITEM" ]; then
  echo "error: $REF_CANON is not on the SimpleSalt Delivery board (number $BOARD_NUMBER)" >&2
  exit 1
fi

field_value_from_item() {
  local field_name="$1"
  echo "$ITEM" | jq -r --arg f "$field_name" '
    [(.fieldValues.nodes // [])[] | select(.field.name == $f) | (.name // .text // "")] | first // ""
  '
}

STATUS=$(field_value_from_item "Status")
ASSIGNED=$(field_value_from_item "Assigned")
IN_USE=$(field_value_from_item "In Use")

PARENT=$(echo "$ISSUE" | jq -r '
  if .parent then "\(.parent.repository.nameWithOwner)#\(.parent.number)" else "" end
' 2>/dev/null)

BLOCKERS=$(echo "$ISSUE" | jq -c '
  [(.blockedBy.nodes // [])[] | "\(.repository.nameWithOwner)#\(.number)"]
' 2>/dev/null)

OPEN_CHILDREN=$(echo "$ISSUE" | jq '
  [(.subIssues.nodes // [])[] | select(.state == "OPEN")] | length
' 2>/dev/null)

OPEN_BLOCKERS=$(echo "$ISSUE" | jq '
  [(.subIssues.nodes // [])[] | "\(.repository.nameWithOwner)#\(.number)"] as $children
  | [(.blockedBy.nodes // [])[]
     | select(.state == "OPEN")
     | "\(.repository.nameWithOwner)#\(.number)"
     | select(IN($children[]) | not)]
  | length
' 2>/dev/null)

if ! CHILDREN=$(echo "$ISSUE" | jq -c --argjson n "$BOARD_NUMBER" '
  [(.subIssues.nodes // [])[]
   | . as $c
   | ([(.projectItems.nodes // [])[] | select(.project.number == $n)] | first // null) as $item
   | {
       ref: "\($c.repository.nameWithOwner)#\($c.number)",
       title: ($c.title // ""),
       state: (($c.state // "") | ascii_downcase),
       status: ([($item.fieldValues.nodes // [])[] | select(.field.name == "Status") | .name] | first // ""),
       priority: ([($item.fieldValues.nodes // [])[] | select(.field.name == "Priority") | .number] | first // null)
     }]
' 2>/dev/null) || [ -z "$CHILDREN" ]; then
  echo "error: could not read the child Efforts of $REF_CANON from the board response" >&2
  exit 1
fi

OPEN_DEFERRED_CHILDREN=$(echo "$CHILDREN" | jq '
  [.[] | select(.state == "open" and .status == "Deferred")] | length
')

[ -z "$PARENT" ] && PARENT=""
[ -z "$BLOCKERS" ] && BLOCKERS="[]"
[ -z "$OPEN_CHILDREN" ] && OPEN_CHILDREN=0
[ -z "$OPEN_BLOCKERS" ] && OPEN_BLOCKERS=0

ITEM_ID=$(echo "$ITEM" | jq -r '.id // empty')

jq -n \
  --arg ref "$REF_CANON" \
  --arg title "$TITLE" \
  --arg state "$STATE" \
  --arg status "$STATUS" \
  --arg assigned "$ASSIGNED" \
  --arg in_use "$IN_USE" \
  --arg parent "$PARENT" \
  --argjson blockers "$BLOCKERS" \
  --arg body "$BODY" \
  --argjson open_children "$OPEN_CHILDREN" \
  --argjson open_blockers "$OPEN_BLOCKERS" \
  --arg item_id "$ITEM_ID" \
  --argjson children "$CHILDREN" \
  --argjson open_deferred_children "$OPEN_DEFERRED_CHILDREN" \
  '{ref: $ref, title: $title, state: $state, status: $status, assigned: $assigned, in_use: $in_use, parent: $parent, blockers: $blockers, body: $body, open_children: $open_children, open_blockers: $open_blockers, item_id: $item_id, children: $children, open_deferred_children: $open_deferred_children}'
