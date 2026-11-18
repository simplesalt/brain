set -euo pipefail

HINDSIGHT_CONFIG_FILE="${HINDSIGHT_CONFIG_FILE:-$HOME/.hindsight/claude-code.json}"

_hs_config_get() {
  local key="$1"
  [ -f "$HINDSIGHT_CONFIG_FILE" ] || return 0
  jq -r --arg k "$key" '.[$k] // empty' "$HINDSIGHT_CONFIG_FILE" 2>/dev/null
}

HS_API_URL="${HINDSIGHT_API_URL:-$(_hs_config_get hindsightApiUrl)}"
HS_API_TOKEN="${HINDSIGHT_API_TOKEN:-$(_hs_config_get hindsightApiToken)}"
HS_BANK_ID="${HINDSIGHT_BANK_ID:-$(_hs_config_get bankId)}"

if [ -z "$HS_API_URL" ] || [ -z "$HS_BANK_ID" ]; then
  echo "error: Hindsight not configured — set HINDSIGHT_API_URL/HINDSIGHT_BANK_ID or populate $HINDSIGHT_CONFIG_FILE" >&2
  exit 1
fi

hs_bank_path() {
  local suffix="$1"
  local encoded
  encoded=$(jq -rn --arg b "$HS_BANK_ID" '$b|@uri')
  echo "/v1/default/banks/${encoded}/${suffix}"
}

hs_uri_encode() {
  jq -rn --arg s "$1" '$s|@uri'
}

hs_request() {
  local method="$1" path="$2" body="${3:-}"
  local url="${HS_API_URL}${path}"
  local args=(-sS -m 20 -w '\n%{http_code}' -X "$method" -H "Authorization: Bearer ${HS_API_TOKEN}" -H "Content-Type: application/json")
  if [ -n "$body" ]; then
    args+=(-d "$body")
  fi
  local out status
  out=$(curl "${args[@]}" "$url")
  status="${out##*$'\n'}"
  out="${out%$'\n'*}"
  if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
    echo "error: HTTP $status from $method $path: $out" >&2
    return 1
  fi
  echo "$out"
}
