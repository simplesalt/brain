#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/lib-effort.sh"

USAGE="usage: find-effort.sh [--why <text>] [--what <text>] [--where <text>] [--limit N]"

WHY=""
WHAT=""
WHERE=""
LIMIT=15

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) echo "$USAGE"; exit 0 ;;
    --why)   WHY="${2:?--why requires a value}"; shift 2 ;;
    --what)  WHAT="${2:?--what requires a value}"; shift 2 ;;
    --where) WHERE="${2:?--where requires a value}"; shift 2 ;;
    --limit) LIMIT="${2:?--limit requires a value}"; shift 2 ;;
    *) echo "error: unknown argument '$1'" >&2; echo "$USAGE" >&2; exit 1 ;;
  esac
done

QUERY_TMP=$(mktemp)
if ! effort_search_query "$WHY" "$WHAT" "$WHERE" > "$QUERY_TMP"; then
  rm -f "$QUERY_TMP"
  echo "$USAGE" >&2
  exit 1
fi
QUERY=$(cat "$QUERY_TMP")
rm -f "$QUERY_TMP"

TOKENS="$EFFORT_QUERY_TOKENS"
read -r -a TOKENS_ARR <<< "$TOKENS"
TOTAL="${#TOKENS_ARR[@]}"

MATCHES=$(search_efforts "$QUERY" "$LIMIT")

echo "$MATCHES" | jq -c '.[]' | while IFS= read -r item; do
  [ -z "$item" ] && continue
  num=$(jq -r '.number' <<<"$item")
  title=$(jq -r '.title' <<<"$item")
  body=$(jq -r '.body // ""' <<<"$item")
  read -r matched score < <(effort_relevance "$title" "$body" "$TOKENS")
  score_cents=$((10#${score/./}))
  keep=1
  [ "$score_cents" -ge 25 ] || keep=0
  if [ "$TOTAL" -ge 3 ] && [ "$matched" -lt 2 ]; then
    keep=0
  fi
  [ "$keep" -eq 1 ] || continue
  resolve_ref "$DEFAULT_REPO#$num"
  jq -c --arg ref "$REF_CANON" --argjson score "$score" --argjson matched "$matched" \
    '{ref: $ref, number, title, url, score: $score, matched: $matched}' <<<"$item"
done | jq -s 'sort_by(-.score)'
