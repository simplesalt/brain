SCRIPT_DIR_LIB_EFFORT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR_LIB_EFFORT/lib-project.sh"

EFFORT_TYPES="Feature Bug Analysis Advisory"
EFFORT_BANKS="Dylan Russ ssint-build-basis ssint-build-brain ssint-build-cloud-basis ssint-build-oci ssint-build-projects"

validate_effort_type() {
  local t="$1" name
  for name in $EFFORT_TYPES; do
    [ "$name" = "$t" ] && return 0
  done
  echo "error: --type must be one of: $EFFORT_TYPES (got '$t')" >&2
  return 1
}

validate_effort_assigned() {
  local a="$1" name
  for name in $EFFORT_BANKS; do
    [ "$name" = "$a" ] && return 0
  done
  echo "error: --assigned must be one of: $EFFORT_BANKS (got '$a')" >&2
  return 1
}

EFFORT_TOKENISER_PY_SRC=$(cat <<'PYEOF'
import re

STOPWORDS = {
    "the", "and", "for", "that", "this", "these", "those", "which", "who",
    "whom", "whose", "what", "where", "why", "how", "when", "while", "with",
    "without", "then", "else", "than", "from", "into", "onto", "over",
    "under", "above", "below", "between", "after", "before", "again",
    "against", "about", "because", "each", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "very", "just", "also", "there",
    "here", "once", "through", "during", "further", "having", "does",
    "doesn", "doing", "done", "isn", "aren", "wasn", "weren", "hasn",
    "haven", "hadn", "wouldn", "couldn", "shouldn", "mustn", "won", "don",
    "they", "them", "their", "theirs", "themselves", "she", "her", "hers",
    "herself", "him", "his", "himself", "you", "your", "yours", "yourself",
    "yourselves", "we", "our", "ours", "ourselves", "itself", "being",
    "been", "have", "has", "had", "will", "would", "should", "could",
    "shall", "must", "might", "cannot", "not", "any", "all", "both",
    "still", "yet", "even", "back", "already", "always", "never",
    "keep", "keeps", "kept", "stay", "stays", "stayed", "every", "everyone",
    "everything", "everywhere", "someone", "something", "somewhere",
    "anyone", "anything", "anywhere", "nobody", "nothing", "none", "ever",
    "often", "sometimes", "usually", "really", "quite", "rather", "instead",
    "whether", "either", "neither", "since", "until", "unless", "though",
    "although", "however", "therefore", "thus", "hence", "otherwise",
    "make", "makes", "made", "making", "take", "takes", "taken", "taking",
    "gets", "getting", "give", "gives", "given", "giving", "uses", "used",
    "using", "need", "needs", "needed", "want", "wants", "wanted", "know",
    "knows", "known", "like", "well", "many", "much", "next", "last",
    "first", "second", "another", "others", "thing", "things", "way",
    "ways", "within", "across", "along", "around", "upon", "toward",
    "towards", "whenever", "wherever", "whatever", "whichever", "able",
    "unable", "allow", "allows", "allowed", "work", "works", "worked",
    "working", "exist", "exists", "existing", "becomes", "become", "came",
    "come", "comes", "goes", "going", "gone", "went", "seen", "sees",
    "said", "says", "saying", "tell", "tells", "told", "sure", "true",
    "false", "different", "current", "currently", "new", "old", "real",
    "really", "right", "wrong", "good", "bad", "part", "parts", "whole",
    "fine", "ok", "okay",
}


def tokens(text):
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    words = text.split()
    return [w for w in words if len(w) >= 4 and w not in STOPWORDS]
PYEOF
)
export EFFORT_TOKENISER_PY_SRC

effort_search_query() {
  local why="$1" what="$2" where="$3"
  local combined="$why $what $where"
  if [ -z "${combined// }" ]; then
    echo "error: at least one of --why/--what/--where is required" >&2
    return 1
  fi
  local tokens
  tokens=$(python3 - "$combined" <<'PYEOF'
import os
import sys

exec(os.environ["EFFORT_TOKENISER_PY_SRC"])

words = tokens(sys.argv[1])

seen = set()
kept = []
for word in words:
    if word in seen:
        continue
    seen.add(word)
    kept.append(word)
    if len(kept) >= 12:
        break

print(" ".join(kept))
PYEOF
)
  if [ -z "$tokens" ]; then
    echo "error: at least one of --why/--what/--where is required" >&2
    return 1
  fi
  local -a all_tokens
  read -r -a all_tokens <<< "$tokens"
  EFFORT_QUERY_TOKENS="${all_tokens[*]}"
  local -r prefix="repo:$DEFAULT_REPO is:issue is:open ("
  local -r suffix=")"
  local -r max_query_chars=240
  local -r max_query_terms=6
  local clause="" tok candidate total_len
  local -a used_tokens=()
  for tok in "${all_tokens[@]}"; do
    if [ "${#used_tokens[@]}" -ge "$max_query_terms" ]; then
      break
    fi
    if [ -z "$clause" ]; then
      candidate="$tok"
    else
      candidate="$clause OR $tok"
    fi
    total_len=$(( ${#prefix} + ${#candidate} + ${#suffix} ))
    if [ "$total_len" -gt "$max_query_chars" ]; then
      break
    fi
    clause="$candidate"
    used_tokens+=("$tok")
  done
  echo "${prefix}${clause}${suffix}"
}

search_efforts() {
  local query="$1" limit="${2:-15}"
  gh api -X GET search/issues -f "q=${query}" -f "per_page=${limit}" \
    --jq '[.items[] | {number, title, url: .html_url, body}]'
}

effort_relevance() {
  local title="$1" body="$2" query_tokens="$3"
  EFFORT_SCHEMA_DIR="$SCRIPT_DIR_LIB_EFFORT" python3 - "$title" "$body" "$query_tokens" <<'PYEOF'
import os
import sys

sys.path.insert(0, os.environ["EFFORT_SCHEMA_DIR"])
import effort_schema

exec(os.environ["EFFORT_TOKENISER_PY_SRC"])

title = sys.argv[1]
body = sys.argv[2]
query_tokens = sys.argv[3].split()

IDENTITY_SECTIONS = ("Title", "Why", "What", "Where", "Tags")

sections = effort_schema.parse(body)["sections"]
if any(sections.get(name) for name in effort_schema.SECTION_ORDER):
    parts = [title]
    for name in IDENTITY_SECTIONS:
        value = sections.get(name)
        if value:
            parts.append(value)
    candidate_text = "\n".join(parts)
else:
    candidate_text = title + "\n" + body[:400]

candidate_words = set(tokens(candidate_text))

total = len(query_tokens)
matched = sum(1 for t in query_tokens if t in candidate_words)
score = (matched / total) if total else 0.0
print(f"{matched} {score:.2f}")
PYEOF
}

field_text_value() {
  local item_id="$1" field_name="$2" raw rc value
  raw=$(gh api graphql -f query='
    query($item:ID!, $field:String!) {
      node(id:$item) {
        ... on ProjectV2Item {
          fieldValueByName(name:$field) {
            ... on ProjectV2ItemFieldTextValue { text }
          }
        }
      }
    }' -f item="$item_id" -f field="$field_name" 2>/dev/null)
  rc=$?
  if [ "$rc" -ne 0 ]; then
    return 2
  fi
  value=$(echo "$raw" | jq -r '.data.node.fieldValueByName.text // empty' 2>/dev/null)
  printf '%s' "$value"
  return 0
}

set_text_field_value() {
  local item_id="$1" field_name="$2" value="$3"
  local pid field_id
  pid=$(project_id)
  field_id=$(resolve_field_id "$field_name")
  gh api graphql -f query='
    mutation($project:ID!, $item:ID!, $field:ID!, $value:String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $project
        itemId: $item
        fieldId: $field
        value: { text: $value }
      }) { projectV2Item { id } }
    }' -f project="$pid" -f item="$item_id" -f field="$field_id" -f value="$value" \
    --jq '.data.updateProjectV2ItemFieldValue.projectV2Item.id' >/dev/null
}

set_number_field() {
  local item_id="$1" field_name="$2" number="$3"
  local pid field_id raw
  if ! jq -en --arg v "$number" '$v | tonumber' >/dev/null 2>&1; then
    echo "error: '$number' is not a number — refusing to set the $field_name field" >&2
    return 1
  fi
  pid=$(project_id)
  field_id=$(resolve_field_id "$field_name")
  raw=$(jq -n \
    --arg project "$pid" --arg item "$item_id" --arg field "$field_id" --arg value "$number" \
    '{
      query: "mutation($project:ID!, $item:ID!, $field:ID!, $value:Float!) { updateProjectV2ItemFieldValue(input: { projectId: $project, itemId: $item, fieldId: $field, value: { number: $value } }) { projectV2Item { id } } }",
      variables: { project: $project, item: $item, field: $field, value: ($value | tonumber) }
    }' | gh api graphql --input -)
  jq -e '.data.updateProjectV2ItemFieldValue.projectV2Item.id' <<<"$raw" >/dev/null
}

board_item_priorities() {
  local cursor="" raw page acc="[]" has_next
  local -a cursor_args
  while :; do
    cursor_args=()
    [ -n "$cursor" ] && cursor_args=(-f after="$cursor")
    raw=$(gh api graphql -f query='
      query($org:String!, $number:Int!, $after:String) {
        organization(login:$org) {
          projectV2(number:$number) {
            items(first:100, after:$after) {
              pageInfo { hasNextPage endCursor }
              nodes {
                id
                fieldValueByName(name:"Priority") {
                  ... on ProjectV2ItemFieldNumberValue { number }
                }
              }
            }
          }
        }
      }' -f org="$ORG" -F number="$BOARD_NUMBER" ${cursor_args[@]+"${cursor_args[@]}"}) || return 1
    if ! page=$(jq -ce '
      .data.organization.projectV2.items
      | [.nodes[] | select(.fieldValueByName.number != null) | {id: .id, priority: .fieldValueByName.number}]
    ' <<<"$raw"); then
      echo "error: could not read the board's items from the API response" >&2
      return 1
    fi
    acc=$(jq -c --argjson page "$page" '. + $page' <<<"$acc")
    has_next=$(jq -r '.data.organization.projectV2.items.pageInfo.hasNextPage' <<<"$raw")
    [ "$has_next" = "true" ] || break
    cursor=$(jq -r '.data.organization.projectV2.items.pageInfo.endCursor' <<<"$raw")
  done
  printf '%s\n' "$acc"
}

set_singleselect_field() {
  local item_id="$1" field_name="$2" option_name="$3"
  local pid field_id option_id
  pid=$(project_id)
  read -r field_id option_id < <(resolve_field_option "$field_name" "$option_name")
  gh api graphql -f query='
    mutation($project:ID!, $item:ID!, $field:ID!, $option:String!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $project
        itemId: $item
        fieldId: $field
        value: { singleSelectOptionId: $option }
      }) { projectV2Item { id } }
    }' -f project="$pid" -f item="$item_id" -f field="$field_id" -f option="$option_id" \
    --jq '.data.updateProjectV2ItemFieldValue.projectV2Item.id' >/dev/null
}

# Every issue in $DEFAULT_REPO is an Effort (simplesalt/projects#885). An issue there that
# has no board row yet is adopted on first use: the row is added, Status is set from the
# issue's state (open -> Planning, closed completed -> Done, closed otherwise -> Cancelled)
# and Assigned from the body's Assigned section when that names a known bank. Issues in
# other repos are never adopted. EFFORT_NO_ADOPT=1 (the read-only doer endpoint) forbids it.
adoption_allowed() {
  [ "$REF_OWNER/$REF_REPO" = "$DEFAULT_REPO" ] && [ "${EFFORT_NO_ADOPT:-}" != "1" ]
}

adopt_board_item() {
  local raw node state reason body content_id pid item_id status assigned name
  raw=$(gh api graphql -f query='
    query($owner:String!, $repo:String!, $number:Int!) {
      repository(owner:$owner, name:$repo) { issue(number:$number) { id state stateReason body } }
    }' -f owner="$REF_OWNER" -f repo="$REF_REPO" -F number="$REF_NUMBER" 2>/dev/null) || {
    echo "error: could not read $REF_CANON to add it to the SimpleSalt Delivery board (API call failed)" >&2
    return 1
  }
  node=$(jq -c '.data.repository.issue // empty' <<<"$raw" 2>/dev/null)
  if [ -z "$node" ]; then
    echo "error: $REF_CANON could not be fetched" >&2
    return 1
  fi
  content_id=$(jq -r '.id' <<<"$node")
  state=$(jq -r '.state // ""' <<<"$node")
  reason=$(jq -r '.stateReason // ""' <<<"$node")
  body=$(jq -r '.body // ""' <<<"$node")
  pid=$(project_id)
  item_id=$(gh api graphql -f query='
    mutation($project:ID!, $content:ID!) {
      addProjectV2ItemById(input:{projectId:$project, contentId:$content}) { item { id } }
    }' -f project="$pid" -f content="$content_id" --jq '.data.addProjectV2ItemById.item.id' 2>/dev/null) || item_id=""
  if [ -z "$item_id" ]; then
    echo "error: could not add $REF_CANON to the SimpleSalt Delivery board" >&2
    return 1
  fi
  case "$state:$reason" in
    OPEN:*)             status="Planning" ;;
    CLOSED:COMPLETED)   status="Done" ;;
    *)                  status="Cancelled" ;;
  esac
  set_singleselect_field "$item_id" "Status" "$status" || return 1
  assigned=$(python3 -c '
import re, sys
m = re.search(r"^### Assigned[ \t]*\n+([^\n#][^\n]*)", sys.argv[1], re.M)
print(m.group(1).strip() if m else "")
' "$body" 2>/dev/null) || assigned=""
  if [ -n "$assigned" ]; then
    for name in $EFFORT_BANKS; do
      if [ "$name" = "$assigned" ]; then
        set_singleselect_field "$item_id" "Assigned" "$assigned" || return 1
        break
      fi
    done
  fi
  echo "note: $REF_CANON was not on the SimpleSalt Delivery board; added it with Status $status" >&2
  printf '%s' "$item_id"
}

# Board row id for $REF_*, adopting the issue when it has none. Returns 1 when the issue
# has no row and may not be adopted, 2 when the board cannot be read.
ensure_board_item() {
  local id rc
  if id=$(project_item_id); then
    printf '%s' "$id"
    return 0
  else
    rc=$?
  fi
  [ "$rc" -eq 2 ] && return 2
  adoption_allowed || return 1
  id=$(adopt_board_item) || return 2
  printf '%s' "$id"
}

effort_body_field() {
  local body="$1" heading="$2" tmpfile result
  tmpfile=$(mktemp)
  printf '%s' "$body" > "$tmpfile"
  result=$(python3 "$SCRIPT_DIR_LIB_EFFORT/effort_schema.py" parse --body-file "$tmpfile" \
    | jq -r --arg h "$heading" '.sections[$h] // ""')
  rm -f "$tmpfile"
  printf '%s' "$result"
}

effort_body() {
  local title="$1" assigned="$2" parent="$3" blockers="$4" why="$5" what="$6" \
    indicators="$7" gaps="$8" where="$9" facts="${10}" unknowns="${11}" how="${12}" tags="${13}"
  local tmpfile
  tmpfile=$(mktemp)
  jq -n \
    --arg Title "$title" --arg Assigned "$assigned" --arg Parent "$parent" \
    --arg Blockers "$blockers" --arg Why "$why" --arg What "$what" \
    --arg Indicators "$indicators" --arg Gaps "$gaps" --arg Where "$where" \
    --arg Facts "$facts" --arg Unknowns "$unknowns" --arg How "$how" --arg Tags "$tags" \
    '{sections: {Title:$Title, Assigned:$Assigned, Parent:$Parent, Blockers:$Blockers,
      Why:$Why, What:$What, Indicators:$Indicators, Gaps:$Gaps, Where:$Where,
      Facts:$Facts, Unknowns:$Unknowns, How:$How, Tags:$Tags}}' > "$tmpfile"
  python3 "$SCRIPT_DIR_LIB_EFFORT/effort_schema.py" render --json-file "$tmpfile"
  printf '\n'
  rm -f "$tmpfile"
}
