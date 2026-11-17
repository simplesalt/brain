#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib-hindsight.sh"

USAGE="usage: hindsight-get-document.sh <document_id> [--text-only]"

document_id="${1:?$USAGE}"
shift

text_only=""

while [ $# -gt 0 ]; do
  case "$1" in
    --text-only) text_only="1"; shift ;;
    *) echo "error: unknown argument '$1'" >&2; echo "$USAGE" >&2; exit 1 ;;
  esac
done

path=$(hs_bank_path "documents/$(hs_uri_encode "$document_id")")
doc=$(hs_request GET "$path")

if [ -n "$text_only" ]; then
  echo "$doc" | jq -r '.original_text'
else
  echo "$doc"
fi
