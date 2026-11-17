#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib-hindsight.sh"

page_id="${1:?usage: hindsight-get-page.sh <page_id>}"

path=$(hs_bank_path "mental-models/$(hs_uri_encode "$page_id")")
hs_request GET "${path}?detail=content"
