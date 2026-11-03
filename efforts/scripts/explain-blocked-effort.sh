#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/lib-effort.sh"

USAGE="usage: explain-blocked-effort.sh (<ref> | --all)"

ALL_MODE=0
REF_ARG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --all)
      ALL_MODE=1
      shift
      ;;
    -*)
      echo "error: unknown argument '$1'" >&2
      echo "$USAGE" >&2
      exit 1
      ;;
    *)
      if [ -n "$REF_ARG" ]; then
        echo "error: unexpected extra argument '$1'" >&2
        echo "$USAGE" >&2
        exit 1
      fi
      REF_ARG="$1"
      shift
      ;;
  esac
done

if [ "$ALL_MODE" -eq 1 ] && [ -n "$REF_ARG" ]; then
  echo "error: specify either <ref> or --all, not both" >&2
  echo "$USAGE" >&2
  exit 1
fi

if [ "$ALL_MODE" -eq 0 ] && [ -z "$REF_ARG" ]; then
  echo "error: $USAGE" >&2
  exit 1
fi

BATCH_SIZE=15

GRAPH_FILE=$(mktemp)
FRONTIER_FILE=$(mktemp)
TMP_RAW=$(mktemp)
trap 'rm -f "$GRAPH_FILE" "$GRAPH_FILE.next" "$FRONTIER_FILE" "$TMP_RAW"' EXIT

echo '{}' > "$GRAPH_FILE"

fetch_batch() {
  local ids_json="$1"
  local n
  n=$(jq 'length' <<<"$ids_json")
  if [ "$n" -eq 0 ]; then
    echo '{}'
    return 0
  fi
  local result='{}'
  local i=0
  while [ "$i" -lt "$n" ]; do
    local batch_ids m var_decls query_parts args j id raw piece
    batch_ids=$(jq -c ".[$i:$((i + BATCH_SIZE))]" <<<"$ids_json")
    m=$(jq 'length' <<<"$batch_ids")
    var_decls=""
    query_parts=""
    args=()
    j=0
    while [ "$j" -lt "$m" ]; do
      id=$(jq -r ".[$j]" <<<"$batch_ids")
      var_decls+="\$id$j:ID!,"
      query_parts+="i$j: node(id:\$id$j) { id ... on Issue { number title url state stateReason repository { nameWithOwner } blockedBy(first:50) { nodes { id state } } projectItems(first:20) { nodes { project { number } status: fieldValueByName(name:\"Status\") { ... on ProjectV2ItemFieldSingleSelectValue { name } } assigned: fieldValueByName(name:\"Assigned\") { ... on ProjectV2ItemFieldSingleSelectValue { name } } inuse: fieldValueByName(name:\"In Use\") { ... on ProjectV2ItemFieldTextValue { text } } } } } } "
      args+=(-f "id$j=$id")
      j=$((j + 1))
    done
    var_decls="${var_decls%,}"
    raw=$(gh api graphql -f query="query($var_decls) { $query_parts }" "${args[@]}")
    piece=$(jq -c --argjson board "$BOARD_NUMBER" '
      .data
      | to_entries
      | map(select(.value != null))
      | map({
          key: .value.id,
          value: {
            number: .value.number,
            title: .value.title,
            url: .value.url,
            state: .value.state,
            stateReason: .value.stateReason,
            repo: .value.repository.nameWithOwner,
            status: ((.value.projectItems.nodes[]? | select(.project.number == $board) | .status.name) // ""),
            assigned: ((.value.projectItems.nodes[]? | select(.project.number == $board) | .assigned.name) // ""),
            inuse: ((.value.projectItems.nodes[]? | select(.project.number == $board) | .inuse.text) // ""),
            blockers: [(.value.blockedBy.nodes // [])[] | {id, state}]
          }
        })
      | from_entries
    ' <<<"$raw")
    result=$(jq -sc '.[0] * .[1]' <(echo "$result") <(echo "$piece"))
    i=$((i + BATCH_SIZE))
  done
  echo "$result"
}

grow_graph() {
  local initial_ids_json="$1"
  echo "$initial_ids_json" > "$FRONTIER_FILE"
  local depth=0
  while :; do
    local frontier fcount
    frontier=$(cat "$FRONTIER_FILE")
    fcount=$(jq 'length' <<<"$frontier")
    if [ "$fcount" -eq 0 ]; then
      break
    fi
    if [ "$depth" -gt "$MAX_TRAVERSAL_DEPTH" ]; then
      break
    fi
    local to_fetch tfcount
    to_fetch=$(jq -c --slurpfile g "$GRAPH_FILE" '(. - ($g[0] | keys)) | unique' <<<"$frontier")
    tfcount=$(jq 'length' <<<"$to_fetch")
    if [ "$tfcount" -gt 0 ]; then
      local new
      new=$(fetch_batch "$to_fetch")
      jq -sc '.[0] * .[1]' "$GRAPH_FILE" <(echo "$new") > "$GRAPH_FILE.next"
      mv "$GRAPH_FILE.next" "$GRAPH_FILE"
    fi
    local next
    next=$(jq -c --slurpfile g "$GRAPH_FILE" '
      ([ .[] as $id | ($g[0][$id] // null) | select(. != null and .state == "OPEN") | (.blockers // [])[] | .id ] | unique)
      - ($g[0] | keys)
    ' <<<"$frontier")
    echo "$next" > "$FRONTIER_FILE"
    depth=$((depth + 1))
  done
}

declare -A CLASS_KIND
declare -A CLASS_CLASS
declare -A CLASS_REASON
declare -A ON_STACK

get_record() {
  jq -c --arg id "$1" '.[$id] // empty' "$GRAPH_FILE"
}

resolve_node() {
  local id="$1"
  if [ -n "${CLASS_KIND[$id]+x}" ]; then
    return 0
  fi
  if [ -n "${ON_STACK[$id]+x}" ]; then
    CLASS_KIND[$id]="unknown"
    CLASS_CLASS[$id]="unknown"
    CLASS_REASON[$id]="dependency cycle detected while walking this chain"
    return 0
  fi
  ON_STACK[$id]=1
  local rec
  rec=$(get_record "$id")
  if [ -z "$rec" ]; then
    CLASS_KIND[$id]="unknown"
    CLASS_CLASS[$id]="unknown"
    CLASS_REASON[$id]="could not be resolved within the traversal depth cap of $MAX_TRAVERSAL_DEPTH hops"
    unset "ON_STACK[$id]"
    return 0
  fi
  local state
  state=$(jq -r '.state' <<<"$rec")
  if [ "$state" = "CLOSED" ]; then
    local reason
    reason=$(jq -r '.stateReason // "null"' <<<"$rec")
    case "$reason" in
      NOT_PLANNED)
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="root-cancelled"
        ;;
      COMPLETED)
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="root-done"
        ;;
      *)
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="unknown"
        CLASS_REASON[$id]="closed with stateReason='$reason', not a recognized terminal reason"
        ;;
    esac
  else
    local open_children occ
    open_children=$(jq -c '[(.blockers // [])[] | select(.state == "OPEN") | .id]' <<<"$rec")
    occ=$(jq 'length' <<<"$open_children")
    if [ "$occ" -eq 0 ]; then
      local status inuse
      status=$(jq -r '.status // ""' <<<"$rec")
      inuse=$(jq -r '.inuse // ""' <<<"$rec")
      if [ "$status" = "Deferred" ]; then
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="root-deferred"
        CLASS_REASON[$id]="open and Deferred: in the backlog, nobody is working it now"
      elif [ -n "$inuse" ]; then
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="root-active"
      elif [ "$status" = "Doing" ]; then
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="root-unheld"
      elif [ "$status" = "Planning" ]; then
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="root-planning"
      elif [ "$status" = "Blocked" ]; then
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="root-clear-blocked"
      elif [ "$status" = "Review" ]; then
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="root-review"
      elif [ "$status" = "Paused" ]; then
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="root-paused"
      else
        CLASS_KIND[$id]="root"
        CLASS_CLASS[$id]="unknown"
        CLASS_REASON[$id]="open with status='$status' and In Use='$inuse', which does not match a recognized root pattern"
      fi
    else
      local cid
      for cid in $(jq -r '.[]' <<<"$open_children"); do
        resolve_node "$cid"
      done
      CLASS_KIND[$id]="continue"
    fi
  fi
  unset "ON_STACK[$id]"
}

root_json() {
  local id="$1"
  local rec cls reason
  rec=$(get_record "$id")
  cls="${CLASS_CLASS[$id]}"
  reason="${CLASS_REASON[$id]:-}"
  jq -c --arg cls "$cls" --arg reason "$reason" '
    {
      number: .number,
      title: .title,
      url: .url,
      repo: .repo,
      ref: (.repo + "#" + (.number | tostring)),
      state: .state,
      status: (.status // ""),
      assigned: (.assigned // ""),
      in_use: (.inuse // ""),
      classification: $cls
    } + (if $reason == "" then {} else {reason: $reason} end)
  ' <<<"$rec"
}

path_json() {
  local out="[]"
  local id rec entry
  for id in "$@"; do
    rec=$(get_record "$id")
    entry=$(jq -c '{ref: (.repo + "#" + (.number | tostring)), number: .number, title: .title}' <<<"$rec")
    out=$(jq -c --argjson e "$entry" '. + [$e]' <<<"$out")
  done
  echo "$out"
}

find_roots_for() {
  local start_id="$1"
  local rec open_count
  rec=$(get_record "$start_id")
  open_count=$(jq '[(.blockers // [])[] | select(.state == "OPEN")] | length' <<<"$rec")
  if [ "$open_count" -eq 0 ]; then
    jq -cn '{classification: "clear", note: "no open blockers; ready to proceed despite board status", roots: []}'
    return 0
  fi

  local -a queue_ids=()
  local -a queue_paths=()
  local blocker_ids bid
  blocker_ids=$(jq -r '[(.blockers // [])[] | .id] | .[]' <<<"$rec")
  while IFS= read -r bid; do
    [ -z "$bid" ] && continue
    queue_ids+=("$bid")
    queue_paths+=("$start_id $bid")
  done <<<"$blocker_ids"

  local -A bfs_visited=()
  local -a seen_root_order=()
  local -A seen_root_path=()
  local head=0

  while [ "$head" -lt "${#queue_ids[@]}" ]; do
    local id path kind
    id="${queue_ids[$head]}"
    path="${queue_paths[$head]}"
    head=$((head + 1))
    if [ -n "${bfs_visited[$id]+x}" ]; then
      continue
    fi
    bfs_visited[$id]=1
    resolve_node "$id"
    kind="${CLASS_KIND[$id]}"
    if [ "$kind" != "continue" ]; then
      if [ -z "${seen_root_path[$id]+x}" ]; then
        seen_root_path[$id]="$path"
        seen_root_order+=("$id")
      fi
    else
      local crec cbids cid
      crec=$(get_record "$id")
      cbids=$(jq -r '[(.blockers // [])[] | .id] | .[]' <<<"$crec")
      while IFS= read -r cid; do
        [ -z "$cid" ] && continue
        if [ -z "${bfs_visited[$cid]+x}" ]; then
          queue_ids+=("$cid")
          queue_paths+=("$path $cid")
        fi
      done <<<"$cbids"
    fi
  done

  local roots_json="[]"
  local rid pth rjson pjson entry
  for rid in "${seen_root_order[@]}"; do
    pth="${seen_root_path[$rid]}"
    rjson=$(root_json "$rid")
    pjson=$(path_json $pth)
    entry=$(jq -c --argjson r "$rjson" --argjson p "$pjson" '$r + {chain: $p}' <<<'{}')
    roots_json=$(jq -c --argjson e "$entry" '. + [$e]' <<<"$roots_json")
  done

  jq -c --argjson roots "$roots_json" '{classification: "blocked", roots: $roots}' <<<'{}'
}

report_effort() {
  local id="$1"
  local rec result
  rec=$(get_record "$id")
  result=$(find_roots_for "$id")
  jq -c --argjson res "$result" '
    {
      ref: (.repo + "#" + (.number | tostring)),
      number: .number,
      title: .title,
      url: .url,
      status: (.status // ""),
      assigned: (.assigned // ""),
      in_use: (.inuse // "")
    } + $res
  ' <<<"$rec"
}

if [ "$ALL_MODE" -eq 1 ]; then
  : > "$TMP_RAW"
  CURSOR="null"
  while :; do
    PAGE=$(gh api graphql -f query='
      query($org:String!, $number:Int!, $cursor:String) {
        organization(login:$org) {
          projectV2(number:$number) {
            items(first:100, after:$cursor) {
              pageInfo { hasNextPage endCursor }
              nodes {
                content {
                  __typename
                  ... on Issue { id number state }
                }
                status: fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
              }
            }
          }
        }
      }' -f org="$ORG" -F number="$BOARD_NUMBER" -F cursor="$CURSOR")

    echo "$PAGE" | jq -c '.data.organization.projectV2.items.nodes[]' >> "$TMP_RAW"

    HAS_NEXT=$(echo "$PAGE" | jq -r '.data.organization.projectV2.items.pageInfo.hasNextPage')
    CURSOR=$(echo "$PAGE" | jq -r '.data.organization.projectV2.items.pageInfo.endCursor')
    [ "$HAS_NEXT" = "true" ] || break
  done

  INITIAL_IDS=$(jq -sc '
    [ .[]
      | select(.content.__typename == "Issue" and .content.state == "OPEN" and (.status.name // "") == "Blocked")
      | .content.id
    ] | unique
  ' "$TMP_RAW")

  grow_graph "$INITIAL_IDS"

  jq -r '.[]' <<<"$INITIAL_IDS" | while IFS= read -r id; do
    report_effort "$id"
  done | jq -sc 'sort_by(.number)'
else
  resolve_ref "$REF_ARG"
  START_ID=$(issue_node_id)
  if [ -z "$START_ID" ]; then
    echo "error: could not resolve $REF_CANON to an issue node id" >&2
    exit 1
  fi
  START_IDS_JSON=$(jq -cn --arg id "$START_ID" '[$id]')
  grow_graph "$START_IDS_JSON"
  report_effort "$START_ID" | jq -sc '.'
fi
