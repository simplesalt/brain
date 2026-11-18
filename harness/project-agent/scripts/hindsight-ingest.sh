#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib-hindsight.sh"

USAGE="usage: <content on stdin> | hindsight-ingest.sh <title> [--tags a,b] [--meta key=value ...] [--append]"

title="${1:?$USAGE}"
shift

tags_csv=""
declare -a meta_pairs=()
update_mode=""

while [ $# -gt 0 ]; do
  case "$1" in
    --tags) tags_csv="${2:?--tags requires a value}"; shift 2 ;;
    --meta) meta_pairs+=("${2:?--meta requires a value}"); shift 2 ;;
    --append) update_mode="append"; shift ;;
    *) echo "error: unknown argument '$1'" >&2; echo "$USAGE" >&2; exit 1 ;;
  esac
done

content="$(cat)"
if [ -z "$content" ]; then
  echo "error: no content on stdin" >&2
  exit 1
fi

doc_id=$(echo "$title" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')

metadata_json="{}"
for pair in "${meta_pairs[@]+"${meta_pairs[@]}"}"; do
  key="${pair%%=*}"
  value="${pair#*=}"
  metadata_json=$(jq -n --argjson m "$metadata_json" --arg k "$key" --arg v "$value" '$m + {($k): $v}')
done

item=$(jq -n --arg c "$content" --arg id "$doc_id" --arg ctx "claude-code" --argjson meta "$metadata_json" \
  '{content: $c, document_id: $id, context: $ctx, metadata: $meta}')

if [ -n "$tags_csv" ]; then
  tags_json=$(jq -Rn --arg csv "$tags_csv" '$csv | split(",")')
  item=$(jq -n --argjson base "$item" --argjson tags "$tags_json" '$base + {tags: $tags}')
fi

if [ -n "$update_mode" ]; then
  item=$(jq -n --argjson base "$item" --arg um "$update_mode" '$base + {update_mode: $um}')
fi

path=$(hs_bank_path "memories")
body=$(jq -n --argjson item "$item" '{items: [$item]}')
hs_request POST "$path" "$body"
