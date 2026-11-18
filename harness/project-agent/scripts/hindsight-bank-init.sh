#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib-hindsight.sh"

USAGE="usage: hindsight-bank-init.sh [--dry-run]"

dry_run=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run="1"; shift ;;
    *) echo "error: unknown argument '$1'" >&2; echo "$USAGE" >&2; exit 1 ;;
  esac
done

retain_mission="Extract durable findings about how this coding session's work went: problems, opportunities, unexpected outcomes, and tracked results, each naming the piece of the system responsible and, where known, the proposed fix. Tag findings with their workstream and relevant connections. Ignore mechanical detail — JSONL record structure, tool_use ids, message or turn counts, raw file listings, and restatements of the transcript itself, which is retained separately as one verbatim document."

observations_mission="Consolidate Observations under the workstream and Connections they concern: a problem, opportunity, unexpected outcome, or tracked result, each carrying the piece of the system responsible and the proposed fix if one exists. Merge duplicates and prefer the most recent account when accounts conflict. Never elevate mechanical detail — session structure, tool call counts, or chunk boundaries — into an Observation node."

updates=$(jq -n \
  --arg retain_mission "$retain_mission" \
  --arg observations_mission "$observations_mission" \
  '{
    retain_strategies: {
      transcript: {
        retain_extraction_mode: "chunks",
        enable_observations: false,
        retain_chunk_size: 4000
      },
      observation: {
        retain_extraction_mode: "concise"
      }
    },
    retain_default_strategy: "observation",
    retain_mission: $retain_mission,
    observations_mission: $observations_mission
  }')

body=$(jq -n --argjson updates "$updates" '{updates: $updates}')

if [ -n "$dry_run" ]; then
  echo "$body"
  exit 0
fi

path=$(hs_bank_path "config")
hs_request PATCH "$path" "$body"
