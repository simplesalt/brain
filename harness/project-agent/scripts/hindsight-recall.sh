#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib-hindsight.sh"

USAGE="usage: hindsight-recall.sh <query> [max_tokens] [--types a,b] [--tags a,b]"

query="${1:?$USAGE}"
shift

max_tokens=1024
if [ $# -gt 0 ] && [[ "$1" != --* ]]; then
  max_tokens="$1"
  shift
fi

types_csv=""
tags_csv=""

while [ $# -gt 0 ]; do
  case "$1" in
    --types) types_csv="${2:?--types requires a value}"; shift 2 ;;
    --tags) tags_csv="${2:?--tags requires a value}"; shift 2 ;;
    *) echo "error: unknown argument '$1'" >&2; echo "$USAGE" >&2; exit 1 ;;
  esac
done

path=$(hs_bank_path "memories/recall")
body=$(jq -n --arg q "$query" --argjson mt "$max_tokens" '{query: $q, max_tokens: $mt, budget: "mid"}')

if [ -n "$types_csv" ]; then
  types_json=$(jq -Rn --arg csv "$types_csv" '$csv | split(",")')
  body=$(jq -n --argjson base "$body" --argjson types "$types_json" '$base + {types: $types}')
fi

if [ -n "$tags_csv" ]; then
  tags_json=$(jq -Rn --arg csv "$tags_csv" '$csv | split(",")')
  body=$(jq -n --argjson base "$body" --argjson tags "$tags_json" '$base + {tags: $tags, tags_match: "any"}')
fi

hs_request POST "$path" "$body"
