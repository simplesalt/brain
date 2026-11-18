#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/lib-effort.sh"

case "${1:-}" in
  -h|--help) echo "usage: held-efforts.sh"; exit 0 ;;
esac

HOLDER="$(hostname)/${CLAUDE_CODE_SESSION_ID:?CLAUDE_CODE_SESSION_ID not set}"

set +e
RAW=$(gh api graphql --paginate -f query='
  query($org:String!, $number:Int!, $endCursor:String) {
    organization(login:$org) {
      projectV2(number:$number) {
        items(first:100, after:$endCursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            content { ... on Issue { number title repository { nameWithOwner } } }
            inUse: fieldValueByName(name:"In Use") { ... on ProjectV2ItemFieldTextValue { text } }
            status: fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
          }
        }
      }
    }
  }' -f org="$ORG" -F number="$BOARD_NUMBER" 2>/dev/null)
RC=$?
set -e

if [ "$RC" -ne 0 ] || [ -z "$RAW" ]; then
  echo "error: could not read the SimpleSalt Delivery board's In Use holders (API call failed)" >&2
  exit 1
fi

jq -s --arg holder "$HOLDER" '
  {holder: $holder, held: [.[].data.organization.projectV2.items.nodes[]
    | select((.inUse.text // "") == $holder and .content.repository != null)
    | {ref: "\(.content.repository.nameWithOwner)#\(.content.number)", title: .content.title, status: (.status.name // "")}]}
' <<<"$RAW"
