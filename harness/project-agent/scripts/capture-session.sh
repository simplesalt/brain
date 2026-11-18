#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/lib-hindsight.sh"

USAGE="usage: capture-session.sh <title> [--session <id>] [--tags a,b] [--meta k=v ...]"

title="${1:?$USAGE}"
shift

session_id="${CLAUDE_CODE_SESSION_ID:-}"
tags_csv=""
declare -a meta_pairs=()

while [ $# -gt 0 ]; do
  case "$1" in
    --session) session_id="${2:?--session requires a value}"; shift 2 ;;
    --tags) tags_csv="${2:?--tags requires a value}"; shift 2 ;;
    --meta) meta_pairs+=("${2:?--meta requires a value}"); shift 2 ;;
    *) echo "error: unknown argument '$1'" >&2; echo "$USAGE" >&2; exit 1 ;;
  esac
done

if [ -z "$session_id" ]; then
  echo "error: no session id — set CLAUDE_CODE_SESSION_ID or pass --session <id>" >&2
  exit 1
fi

slug="$(printf '%s' "$PWD" | tr -c 'a-zA-Z0-9' '-')"
transcript_path="$HOME/.claude/projects/${slug}/${session_id}.jsonl"

if [ ! -f "$transcript_path" ]; then
  echo "error: transcript not found at $transcript_path" >&2
  exit 1
fi

md_file="$(mktemp)"
body_file="$(mktemp)"
trap 'rm -f "$md_file" "$body_file"' EXIT

python3 - "$transcript_path" > "$md_file" <<'PYEOF'
import json
import sys

path = sys.argv[1]

def one_line(s, limit):
    s = " ".join(s.split())
    if len(s) > limit:
        s = s[:limit]
    return s

def result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        return " ".join(parts)
    return ""

out = []

with open(path, "r") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        rtype = rec.get("type")
        if rtype not in ("user", "assistant"):
            continue
        message = rec.get("message") or {}
        content = message.get("content")
        out.append("## " + rtype)
        out.append("")
        if isinstance(content, str):
            if content:
                out.append(content)
                out.append("")
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "thinking":
                continue
            if btype == "text":
                t = block.get("text", "")
                if t:
                    out.append(t)
                    out.append("")
            elif btype == "tool_use":
                name = block.get("name", "")
                inp = block.get("input", {})
                try:
                    inp_str = json.dumps(inp, separators=(",", ":"))
                except (TypeError, ValueError):
                    inp_str = str(inp)
                out.append("- tool: " + name + " " + one_line(inp_str, 120))
            elif btype == "tool_result":
                rtext = result_text(block.get("content"))
                out.append("- result: " + one_line(rtext, 200))
        out.append("")

print("\n".join(out))
PYEOF

if [ ! -s "$md_file" ]; then
  echo "error: rendered transcript is empty" >&2
  exit 1
fi

doc_id=$(echo "$title" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')

metadata_json=$(jq -n --arg s "$session_id" '{session: $s}')
for pair in "${meta_pairs[@]+"${meta_pairs[@]}"}"; do
  key="${pair%%=*}"
  value="${pair#*=}"
  metadata_json=$(jq -n --argjson m "$metadata_json" --arg k "$key" --arg v "$value" '$m + {($k): $v}')
done

tags_json=$(jq -Rn --arg csv "$tags_csv" '($csv | split(",") | map(select(length > 0))) + ["transcript"] | unique')

jq -n \
  --rawfile c "$md_file" \
  --arg id "$doc_id" \
  --arg ctx "claude-code-session" \
  --arg strategy "transcript" \
  --argjson meta "$metadata_json" \
  --argjson tags "$tags_json" \
  '{items: [{content: $c, document_id: $id, context: $ctx, strategy: $strategy, metadata: $meta, tags: $tags}]}' \
  > "$body_file"

path=$(hs_bank_path "memories")
url="${HS_API_URL}${path}"
resp=$(curl -sS -m 30 -w '\n%{http_code}' -X POST \
  -H "Authorization: Bearer ${HS_API_TOKEN}" -H "Content-Type: application/json" \
  --data "@${body_file}" "$url")
status="${resp##*$'\n'}"
resp="${resp%$'\n'*}"
if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
  echo "error: HTTP $status from POST $path: $resp" >&2
  exit 1
fi

doc_path=$(hs_bank_path "documents/$(hs_uri_encode "$doc_id")")
doc=$(hs_request GET "$doc_path")
unit_count=$(echo "$doc" | jq -r '.memory_unit_count // "unknown"')

echo "document_id: ${doc_id}"
echo "memory_unit_count: ${unit_count}"
