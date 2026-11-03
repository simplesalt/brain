ORG="simplesalt"
DEFAULT_REPO="simplesalt/projects"
BOARD_NUMBER=3

BOARD_CACHE_FILE="${HOME}/.claude/board-cache.json"

_board_cache_key() {
  printf '%s:%s' "$ORG" "$BOARD_NUMBER"
}

_board_cache_read() {
  local content
  if [ ! -f "$BOARD_CACHE_FILE" ]; then
    printf '{}'
    return 0
  fi
  content=$(cat "$BOARD_CACHE_FILE" 2>/dev/null)
  if [ -z "$content" ]; then
    printf '{}'
    return 0
  fi
  if ! printf '%s' "$content" | jq -e 'type == "object"' >/dev/null 2>&1; then
    printf '{}'
    return 0
  fi
  printf '%s' "$content"
}

_board_cache_write() {
  local new_content="$1" dir tmp
  dir=$(dirname "$BOARD_CACHE_FILE")
  mkdir -p "$dir" 2>/dev/null || return 0
  tmp=$(mktemp "$dir/.board-cache.XXXXXX" 2>/dev/null) || return 0
  if printf '%s' "$new_content" > "$tmp" 2>/dev/null; then
    mv -f "$tmp" "$BOARD_CACHE_FILE" 2>/dev/null || rm -f "$tmp" 2>/dev/null
  else
    rm -f "$tmp" 2>/dev/null
  fi
  return 0
}

resolve_ref() {
  local ref="$1"
  if [[ "$ref" =~ ^([[:alnum:]_.-]+)/([[:alnum:]_.-]+)#([0-9]+)$ ]]; then
    REF_OWNER="${BASH_REMATCH[1]}"
    REF_REPO="${BASH_REMATCH[2]}"
    REF_NUMBER="${BASH_REMATCH[3]}"
  elif [[ "$ref" =~ ^#?([0-9]+)$ ]]; then
    REF_OWNER="${DEFAULT_REPO%%/*}"
    REF_REPO="${DEFAULT_REPO#*/}"
    REF_NUMBER="${BASH_REMATCH[1]}"
  else
    echo "error: '$ref' is not a valid issue reference (expected owner/repo#N or #N)" >&2
    return 1
  fi
  REF_CANON="$REF_OWNER/$REF_REPO#$REF_NUMBER"
}

issue_node_id() {
  gh api "repos/$REF_OWNER/$REF_REPO/issues/$REF_NUMBER" --jq .node_id
}

project_item_id() {
  local raw rc id
  raw=$(gh api graphql -f query='
    query($owner:String!, $repo:String!, $number:Int!) {
      repository(owner:$owner, name:$repo) {
        issue(number:$number) {
          projectItems(first:20) {
            nodes { id project { number } }
          }
        }
      }
    }' -f owner="$REF_OWNER" -f repo="$REF_REPO" -F number="$REF_NUMBER" 2>/dev/null)
  rc=$?
  if [ "$rc" -ne 0 ]; then
    return 2
  fi
  id=$(echo "$raw" | jq -r --argjson n "$BOARD_NUMBER" '
    (.data.repository.issue.projectItems.nodes // [])[]
    | select(.project.number == $n)
    | .id
  ' 2>/dev/null | head -1)
  if [ -z "$id" ]; then
    return 1
  fi
  printf '%s' "$id"
  return 0
}

project_id() {
  local key cached id rc
  key=$(_board_cache_key)
  cached=$(_board_cache_read | jq -r --arg k "$key" '.[$k].project_id // empty' 2>/dev/null)
  if [ -n "$cached" ]; then
    printf '%s' "$cached"
    return 0
  fi
  id=$(gh api graphql -f query='
    query($org:String!, $number:Int!) {
      organization(login:$org) { projectV2(number:$number) { id } }
    }' -f org="$ORG" -F number="$BOARD_NUMBER" --jq '.data.organization.projectV2.id')
  rc=$?
  if [ "$rc" -eq 0 ] && [ -n "$id" ]; then
    _board_cache_write "$(_board_cache_read | jq --arg k "$key" --arg v "$id" '.[$k].project_id = $v')"
  fi
  printf '%s' "$id"
  return "$rc"
}

resolve_field_option() {
  local field_name="$1" option_name="$2"
  local key cache_json cached_field cached_option
  key=$(_board_cache_key)
  cache_json=$(_board_cache_read)
  cached_field=$(printf '%s' "$cache_json" | jq -r --arg k "$key" --arg f "$field_name" '.[$k].fields[$f] // empty' 2>/dev/null)
  cached_option=$(printf '%s' "$cache_json" | jq -r --arg k "$key" --arg f "$field_name" --arg o "$option_name" '.[$k].field_options[$f][$o] // empty' 2>/dev/null)
  if [ -n "$cached_field" ] && [ -n "$cached_option" ]; then
    echo "$cached_field $cached_option"
    return 0
  fi
  local out
  out=$(gh api graphql -f query='
    query($org:String!, $number:Int!) {
      organization(login:$org) {
        projectV2(number:$number) {
          fields(first:30) {
            nodes {
              ... on ProjectV2SingleSelectField { id name options { id name } }
            }
          }
        }
      }
    }' -f org="$ORG" -F number="$BOARD_NUMBER" \
    --jq ".data.organization.projectV2.fields.nodes[] | select(.name == \"$field_name\")")
  if [ -z "$out" ]; then
    echo "error: no single-select field named '$field_name' on the board" >&2
    return 1
  fi
  local field_id option_id
  field_id=$(echo "$out" | jq -r '.id')
  option_id=$(echo "$out" | jq -r --arg n "$option_name" '.options[] | select(.name == $n) | .id')
  if [ -z "$option_id" ]; then
    echo "error: field '$field_name' has no option named '$option_name'" >&2
    return 1
  fi
  _board_cache_write "$(_board_cache_read | jq --arg k "$key" --arg f "$field_name" --arg fid "$field_id" --arg o "$option_name" --arg oid "$option_id" \
    '.[$k].fields[$f] = $fid | .[$k].field_options[$f][$o] = $oid')"
  echo "$field_id $option_id"
}

resolve_field_id() {
  local field_name="$1"
  local key cached id rc
  key=$(_board_cache_key)
  cached=$(_board_cache_read | jq -r --arg k "$key" --arg f "$field_name" '.[$k].fields[$f] // empty' 2>/dev/null)
  if [ -n "$cached" ]; then
    printf '%s' "$cached"
    return 0
  fi
  id=$(gh api graphql -f query='
    query($org:String!, $number:Int!) {
      organization(login:$org) {
        projectV2(number:$number) {
          fields(first:30) {
            nodes { ... on ProjectV2FieldCommon { id name } }
          }
        }
      }
    }' -f org="$ORG" -F number="$BOARD_NUMBER" \
    --jq ".data.organization.projectV2.fields.nodes[] | select(.name == \"$field_name\") | .id")
  rc=$?
  if [ "$rc" -eq 0 ] && [ -n "$id" ]; then
    _board_cache_write "$(_board_cache_read | jq --arg k "$key" --arg f "$field_name" --arg v "$id" '.[$k].fields[$f] = $v')"
  fi
  printf '%s' "$id"
  return "$rc"
}

current_status_value() {
  local item_id="$1" raw
  raw=$(gh api graphql -f query='
    query($item:ID!) {
      node(id:$item) {
        ... on ProjectV2Item {
          fieldValueByName(name:"Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
      }
    }' -f item="$item_id" 2>/dev/null) || true
  echo "$raw" | jq -r '.data.node.fieldValueByName.name // empty' 2>/dev/null
}

resolve_issue_type_id() {
  local type_name="$1"
  gh api graphql -f query='
    query($org:String!) { organization(login:$org) { issueTypes(first:20) { nodes { id name } } } }' \
    -f org="$ORG" --jq ".data.organization.issueTypes.nodes[] | select(.name == \"$type_name\") | .id"
}

MAX_TRAVERSAL_DEPTH=8

MAX_POLICY_DEPTH=6
MAX_FANOUT=100

node_parent_id() {
  local id="$1" raw
  raw=$(gh api graphql -f query='
    query($id:ID!) { node(id:$id) { ... on Issue { parent { id } } } }' \
    -f id="$id" 2>/dev/null) || true
  echo "$raw" | jq -r '.data.node.parent.id // empty' 2>/dev/null
}

node_children_ids() {
  local id="$1" raw
  raw=$(gh api graphql -f query='
    query($id:ID!) { node(id:$id) { ... on Issue { subIssues(first:100) { nodes { id } } } } }' \
    -f id="$id" 2>/dev/null) || true
  echo "$raw" | jq -r '(.data.node.subIssues.nodes // [])[].id' 2>/dev/null
}

node_blocker_ids() {
  local id="$1" raw
  raw=$(gh api graphql -f query='
    query($id:ID!) { node(id:$id) { ... on Issue { blockedBy(first:50) { nodes { id } } } } }' \
    -f id="$id" 2>/dev/null) || true
  echo "$raw" | jq -r '(.data.node.blockedBy.nodes // [])[].id' 2>/dev/null
}

ancestor_chain_contains() {
  local cur="$1" target="$2" hop
  for ((hop = 0; hop < MAX_TRAVERSAL_DEPTH; hop++)); do
    cur=$(node_parent_id "$cur")
    [ -z "$cur" ] && return 1
    [ "$cur" = "$target" ] && return 0
  done
  return 1
}

descendant_chain_contains() {
  local start="$1" target="$2"
  local -A seen=()
  local queue=("$start")
  local depth
  for ((depth = 0; depth < MAX_TRAVERSAL_DEPTH; depth++)); do
    local next_queue=()
    local id
    for id in "${queue[@]}"; do
      [ -n "${seen[$id]:-}" ] && continue
      seen[$id]=1
      local cid
      while IFS= read -r cid; do
        [ -z "$cid" ] && continue
        [ "$cid" = "$target" ] && return 0
        next_queue+=("$cid")
      done < <(node_children_ids "$id")
    done
    [ "${#next_queue[@]}" -eq 0 ] && return 1
    queue=("${next_queue[@]}")
  done
  return 1
}

blocked_by_reaches() {
  local start="$1" target="$2"
  local -A seen=()
  local queue=("$start")
  local iterations=0
  while [ "${#queue[@]}" -gt 0 ] && [ "$iterations" -lt 200 ]; do
    local next_queue=()
    local id
    for id in "${queue[@]}"; do
      iterations=$((iterations + 1))
      [ -n "${seen[$id]:-}" ] && continue
      seen[$id]=1
      local bid
      while IFS= read -r bid; do
        [ -z "$bid" ] && continue
        [ "$bid" = "$target" ] && return 0
        next_queue+=("$bid")
      done < <(node_blocker_ids "$id")
    done
    queue=("${next_queue[@]}")
  done
  return 1
}

issue_type_name() {
  local node_id="$1" raw
  raw=$(gh api graphql -f query='
    query($id:ID!) { node(id:$id) { ... on Issue { issueType { name } } } }' \
    -f id="$node_id" 2>/dev/null) || true
  echo "$raw" | jq -r '.data.node.issueType.name // empty' 2>/dev/null
}

depth_of() {
  local cur="$1" d=0
  local next
  while [ "$d" -lt "$MAX_TRAVERSAL_DEPTH" ]; do
    next=$(node_parent_id "$cur")
    [ -z "$next" ] && break
    cur="$next"
    d=$((d + 1))
  done
  echo "$d"
}

height_of() {
  local start="$1"
  local -A seen=()
  local queue=("$start")
  local h=0
  local level
  for ((level = 0; level < MAX_TRAVERSAL_DEPTH; level++)); do
    local next_queue=()
    local id
    for id in "${queue[@]}"; do
      [ -n "${seen[$id]:-}" ] && continue
      seen[$id]=1
      local cid
      while IFS= read -r cid; do
        [ -z "$cid" ] && continue
        next_queue+=("$cid")
      done < <(node_children_ids "$id")
    done
    [ "${#next_queue[@]}" -eq 0 ] && break
    h=$((h + 1))
    queue=("${next_queue[@]}")
  done
  echo "$h"
}

guard_policy_depth() {
  local parent_id="$1" parent_canon="$2" child_height="$3" child_canon="$4"
  local action="${5:-folding}"
  local parent_depth total_depth
  parent_depth=$(depth_of "$parent_id")
  total_depth=$((parent_depth + 1 + child_height))
  if [ "$total_depth" -gt "$MAX_POLICY_DEPTH" ]; then
    echo "error: refused — $action $child_canon under $parent_canon would put the tree at depth $total_depth, exceeding the governance policy cap of $MAX_POLICY_DEPTH" >&2
    return 1
  fi
  return 0
}

guard_fanout() {
  local parent_id="$1" parent_canon="$2"
  local child_count
  child_count=$(node_children_ids "$parent_id" | grep -c . || true)
  if [ "$child_count" -ge "$MAX_FANOUT" ]; then
    echo "error: refused — $parent_canon already has $child_count direct children, at the fan-out cap of $MAX_FANOUT" >&2
    return 1
  fi
  return 0
}
