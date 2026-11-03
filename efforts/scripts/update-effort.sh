#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/lib-effort.sh"
EFFORT_SCHEMA="$SCRIPT_DIR/effort_schema.py"

USAGE="usage: update-effort.sh <owner/repo#N> [--status <Planning|Doing|Blocked|Review|Done|Paused|Deferred|Cancelled>] [--force] [--reconcile] [--assigned <bank>] [--why <text>] [--what <text>] [--indicators <text>] [--gaps <text>] [--where <text>] [--facts <text>] [--unknowns <text>] [--how <text>] [--tags <text>] [--add-gap <text>] [--resolve-gap <id> [--note <text>]] [--cancel-gap <id> [--note <text>]] [--add-unknown <text>] [--resolve-unknown <id> [--note <text>]] [--cancel-unknown <id> [--note <text>]] [--add-fact <text> --evidence <ref>] [--add-indicator <text>] [--add-task <text> [--task-blockers <none|refs>] [--task-indicators <text>]] [--check-task <id>] [--cancel-task <id> [--note <text>]]"

case "${1:-}" in
  -h|--help) echo "$USAGE"; exit 0 ;;
esac

REF="${1:?$USAGE}"
shift

STATUS=""
ASSIGNED=""
FORCE=0
RECONCILE=0
declare -A NEWVAL=()
declare -A ISSET=()
UPDATED_FIELDS=()

OP_KIND=()
OP_ARG=()
OP_NOTE=()
OP_EVIDENCE=()
OP_TASK_BLOCKERS=()
OP_TASK_INDICATORS=()

mark_updated() {
  local name="$1" u
  for u in "${UPDATED_FIELDS[@]}"; do
    [ "$u" = "$name" ] && return 0
  done
  UPDATED_FIELDS+=("$name")
}

push_op() {
  OP_KIND+=("$1")
  OP_ARG+=("$2")
  OP_NOTE+=("")
  OP_EVIDENCE+=("")
  OP_TASK_BLOCKERS+=("")
  OP_TASK_INDICATORS+=("")
}

last_op_idx() {
  echo $(( ${#OP_KIND[@]} - 1 ))
}

attach_note() {
  local idx
  idx=$(last_op_idx)
  if [ "$idx" -lt 0 ]; then
    echo "error: --note with no preceding item flag" >&2
    echo "$USAGE" >&2
    exit 1
  fi
  case "${OP_KIND[$idx]}" in
    resolve-gap|cancel-gap|resolve-unknown|cancel-unknown|cancel-task) OP_NOTE[$idx]="$1" ;;
    *)
      echo "error: --note is only valid immediately after --resolve-gap/--cancel-gap/--resolve-unknown/--cancel-unknown/--cancel-task" >&2
      echo "$USAGE" >&2
      exit 1
      ;;
  esac
}

attach_evidence() {
  local idx
  idx=$(last_op_idx)
  if [ "$idx" -lt 0 ] || [ "${OP_KIND[$idx]}" != "add-fact" ]; then
    echo "error: --evidence is only valid immediately after --add-fact" >&2
    echo "$USAGE" >&2
    exit 1
  fi
  OP_EVIDENCE[$idx]="$1"
}

attach_task_blockers() {
  local idx
  idx=$(last_op_idx)
  if [ "$idx" -lt 0 ] || [ "${OP_KIND[$idx]}" != "add-task" ]; then
    echo "error: --task-blockers is only valid immediately after --add-task" >&2
    echo "$USAGE" >&2
    exit 1
  fi
  OP_TASK_BLOCKERS[$idx]="$1"
}

attach_task_indicators() {
  local idx
  idx=$(last_op_idx)
  if [ "$idx" -lt 0 ] || [ "${OP_KIND[$idx]}" != "add-task" ]; then
    echo "error: --task-indicators is only valid immediately after --add-task" >&2
    echo "$USAGE" >&2
    exit 1
  fi
  OP_TASK_INDICATORS[$idx]="$1"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --status)           STATUS="${2:?--status requires a value}"; shift 2 ;;
    --assigned)         ASSIGNED="${2:?--assigned requires a value}"; shift 2 ;;
    --force)            FORCE=1; shift ;;
    --reconcile)        RECONCILE=1; shift ;;
    --why)              NEWVAL[Why]="${2:?--why requires a value}"; ISSET[Why]=1; shift 2 ;;
    --what)             NEWVAL[What]="${2:?--what requires a value}"; ISSET[What]=1; shift 2 ;;
    --indicators)       NEWVAL[Indicators]="${2:?--indicators requires a value}"; ISSET[Indicators]=1; shift 2 ;;
    --gaps)             NEWVAL[Gaps]="${2:?--gaps requires a value}"; ISSET[Gaps]=1; shift 2 ;;
    --where)            NEWVAL[Where]="${2:?--where requires a value}"; ISSET[Where]=1; shift 2 ;;
    --facts)            NEWVAL[Facts]="${2:?--facts requires a value}"; ISSET[Facts]=1; shift 2 ;;
    --unknowns)         NEWVAL[Unknowns]="${2:?--unknowns requires a value}"; ISSET[Unknowns]=1; shift 2 ;;
    --how)              NEWVAL[How]="${2:?--how requires a value}"; ISSET[How]=1; shift 2 ;;
    --tags)              NEWVAL[Tags]="${2:?--tags requires a value}"; ISSET[Tags]=1; shift 2 ;;
    --add-gap)          push_op add-gap "${2:?--add-gap requires a value}"; shift 2 ;;
    --resolve-gap)      push_op resolve-gap "${2:?--resolve-gap requires a value}"; shift 2 ;;
    --cancel-gap)       push_op cancel-gap "${2:?--cancel-gap requires a value}"; shift 2 ;;
    --add-unknown)      push_op add-unknown "${2:?--add-unknown requires a value}"; shift 2 ;;
    --resolve-unknown)  push_op resolve-unknown "${2:?--resolve-unknown requires a value}"; shift 2 ;;
    --cancel-unknown)   push_op cancel-unknown "${2:?--cancel-unknown requires a value}"; shift 2 ;;
    --add-fact)         push_op add-fact "${2:?--add-fact requires a value}"; shift 2 ;;
    --add-indicator)    push_op add-indicator "${2:?--add-indicator requires a value}"; shift 2 ;;
    --add-task)         push_op add-task "${2:?--add-task requires a value}"; shift 2 ;;
    --check-task)       push_op check-task "${2:?--check-task requires a value}"; shift 2 ;;
    --cancel-task)      push_op cancel-task "${2:?--cancel-task requires a value}"; shift 2 ;;
    --note)             attach_note "${2:?--note requires a value}"; shift 2 ;;
    --evidence)         attach_evidence "${2:?--evidence requires a value}"; shift 2 ;;
    --task-blockers)    attach_task_blockers "${2:?--task-blockers requires a value}"; shift 2 ;;
    --task-indicators)  attach_task_indicators "${2:?--task-indicators requires a value}"; shift 2 ;;
    *) echo "error: unknown argument '$1'" >&2; echo "$USAGE" >&2; exit 1 ;;
  esac
done

if [ -z "$STATUS" ] && [ -z "$ASSIGNED" ] && [ "${#ISSET[@]}" -eq 0 ] && [ "${#OP_KIND[@]}" -eq 0 ] && [ "$RECONCILE" -eq 0 ]; then
  echo "error: nothing to update — pass at least one field, or --reconcile to re-evaluate status alone" >&2
  echo "$USAGE" >&2
  exit 1
fi

if [ -n "$STATUS" ]; then
  case "$STATUS" in
    Planning|Doing|Blocked|Review|Done|Paused|Deferred|Cancelled) ;;
    *) echo "error: --status must be one of: Planning Doing Blocked Review Done Paused Deferred Cancelled (got '$STATUS')" >&2; exit 1 ;;
  esac
fi

if [ -n "$ASSIGNED" ]; then
  validate_effort_assigned "$ASSIGNED" || exit 1
  ISSET[Assigned]=1
  NEWVAL[Assigned]="$ASSIGNED"
fi

resolve_ref "$REF"

GET_ERR_TMP=$(mktemp)
if ! RECORD_JSON=$("$SCRIPT_DIR/get-effort.sh" "$REF_CANON" 2>"$GET_ERR_TMP"); then
  cat "$GET_ERR_TMP" >&2
  rm -f "$GET_ERR_TMP"
  exit 1
fi
rm -f "$GET_ERR_TMP"

ITEM_ID=$(jq -r '.item_id' <<<"$RECORD_JSON")
IN_USE=$(jq -r '.in_use' <<<"$RECORD_JSON")
RECORD_STATUS=$(jq -r '.status' <<<"$RECORD_JSON")
BODY=$(jq -r '.body' <<<"$RECORD_JSON")
OPEN_CHILDREN=$(jq -r '.open_children' <<<"$RECORD_JSON")
OPEN_DEFERRED_CHILDREN=$(jq -r '.open_deferred_children' <<<"$RECORD_JSON")
GATE_OPEN_CHILDREN=$((OPEN_CHILDREN - OPEN_DEFERRED_CHILDREN))
OPEN_BLOCKERS=$(jq -r '.open_blockers' <<<"$RECORD_JSON")
TITLE_ORIG=$(jq -r '.title' <<<"$RECORD_JSON")
STATE_ORIG=$(jq -r '.state' <<<"$RECORD_JSON")
ASSIGNED_ORIG=$(jq -r '.assigned' <<<"$RECORD_JSON")
PARENT_ORIG=$(jq -r '.parent' <<<"$RECORD_JSON")
BLOCKERS_ORIG=$(jq -c '.blockers' <<<"$RECORD_JSON")
mapfile -t DEFERRED_CHILD_REFS < <(jq -r '.children[] | select(.state == "open" and .status == "Deferred") | .ref' <<<"$RECORD_JSON")

HOLDER="$(hostname)/${CLAUDE_CODE_SESSION_ID:?CLAUDE_CODE_SESSION_ID not set}"
if [ "$IN_USE" != "$HOLDER" ]; then
  echo "error: $REF_CANON must be selected by this session before updating (run select-effort.sh first) — currently ${IN_USE:-unselected}" >&2
  exit 1
fi

schema_call() {
  local out rc tmp_err
  tmp_err=$(mktemp)
  if out=$(python3 "$EFFORT_SCHEMA" "$@" 2>"$tmp_err"); then
    rc=0
  else
    rc=$?
  fi
  SCHEMA_OUT="$out"
  SCHEMA_ERR="$(cat "$tmp_err")"
  rm -f "$tmp_err"
  return "$rc"
}

item_op_fail() {
  {
    echo "error: refusing to update $REF_CANON — item operation failed:"
    printf '%s\n' "$SCHEMA_ERR"
    echo "nothing was written, no fields were changed — fix the request and retry"
  } >&2
  exit 1
}

fact_evidence_fail() {
  local evid="$1" reason
  if [ -z "$evid" ]; then
    reason="no --evidence was given for \"$2\""
  else
    reason="\"$evid\", offered as evidence for \"$2\", names nothing checkable — point at a command, a path, a URL or a record"
  fi
  {
    echo "error: refusing to update $REF_CANON — a Fact you cannot point at is an Unknown; use --add-unknown instead ($reason)"
    echo "nothing was written, no fields were changed"
  } >&2
  exit 1
}

HAS_BODY_CHANGES=0
if [ "${#ISSET[@]}" -gt 0 ] || [ "${#OP_KIND[@]}" -gt 0 ]; then
  HAS_BODY_CHANGES=1
fi

FINAL_BODY="$BODY"

if [ "$HAS_BODY_CHANGES" -eq 1 ]; then
  if ! grep -q '^### ' <<< "$BODY"; then
    echo "error: $REF_CANON's body does not use the Effort schema headings (### Why, ### What, ...) — refusing to overwrite a non-schema record; migrate its body first" >&2
    exit 1
  fi

  BODY_TMP=$(mktemp)
  printf '%s' "$BODY" > "$BODY_TMP"
  PARSED=$(python3 "$EFFORT_SCHEMA" parse --body-file "$BODY_TMP")

  for name in Title Assigned Parent Blockers Why What Indicators Gaps Where Facts Unknowns How Tags; do
    val=$(jq -r --arg h "$name" '.sections[$h] // ""' <<<"$PARSED")
    declare "ORIG_$name=$val"
    declare "CUR_$name=$val"
  done

  for name in Assigned Why What Indicators Gaps Where Facts Unknowns How Tags; do
    if [ -n "${ISSET[$name]:-}" ]; then
      declare "CUR_$name=${NEWVAL[$name]}"
      case "$name" in
        Assigned) ;;
        *) mark_updated "$(echo "$name" | tr '[:upper:]' '[:lower:]')" ;;
      esac
    fi
  done

  if [ -n "${ISSET[Facts]:-}" ]; then
    BAD_FACT_LINES=()
    while IFS= read -r line; do
      case "$line" in
        "- "*)
          content="${line#- }"
          if ! python3 "$EFFORT_SCHEMA" has-evidence --text "$content" >/dev/null 2>&1; then
            BAD_FACT_LINES+=("$line")
          fi
          ;;
      esac
    done <<< "$CUR_Facts"
    if [ "${#BAD_FACT_LINES[@]}" -gt 0 ]; then
      {
        echo "error: refusing to update $REF_CANON — Facts line(s) without evidence:"
        for l in "${BAD_FACT_LINES[@]}"; do echo "  $l"; done
        echo "nothing was written, no fields were changed — add evidence or use --add-unknown instead"
      } >&2
      rm -f "$BODY_TMP"
      exit 1
    fi
  fi

  INTERMEDIATE_BODY=$(effort_body "$CUR_Title" "$CUR_Assigned" "$CUR_Parent" "$CUR_Blockers" \
    "$CUR_Why" "$CUR_What" "$CUR_Indicators" "$CUR_Gaps" "$CUR_Where" "$CUR_Facts" \
    "$CUR_Unknowns" "$CUR_How" "$CUR_Tags")

  BLANKED=()
  for name in Title Assigned Parent Blockers Why What Indicators Gaps Where Facts Unknowns How Tags; do
    old_var="ORIG_$name"
    new_var="CUR_$name"
    old_meaningful="${!old_var}"
    [ "$old_meaningful" = "none" ] && old_meaningful=""
    new_raw="${!new_var}"
    if [ -n "$old_meaningful" ] && [ -z "$new_raw" ]; then
      BLANKED+=("$name")
    fi
  done

  ISSET_NAMES="${!ISSET[*]}"
  ACCOUNT_FAILED=0
  if ! ACCOUNT_MSG=$(python3 "$EFFORT_SCHEMA" account --body-file "$BODY_TMP" --isset "$ISSET_NAMES"); then
    ACCOUNT_FAILED=1
  fi
  rm -f "$BODY_TMP"

  if [ "${#BLANKED[@]}" -gt 0 ] || [ "$ACCOUNT_FAILED" -eq 1 ]; then
    {
      if [ "${#BLANKED[@]}" -gt 0 ]; then
        echo "error: refusing to update $REF_CANON — rebuilt body would blank non-empty section(s): ${BLANKED[*]}"
      fi
      if [ "$ACCOUNT_FAILED" -eq 1 ]; then
        echo "error: refusing to update $REF_CANON — rebuilt body would silently drop content"
        echo "$ACCOUNT_MSG"
      fi
      echo "nothing was written, no fields were changed — fix $REF_CANON's body by hand first"
    } >&2
    exit 1
  fi

  NEWBODY="$INTERMEDIATE_BODY"
  ITEM_TMP=$(mktemp)
  i=0
  while [ "$i" -lt "${#OP_KIND[@]}" ]; do
    kind="${OP_KIND[$i]}"
    arg="${OP_ARG[$i]}"
    note="${OP_NOTE[$i]}"
    evidence="${OP_EVIDENCE[$i]}"
    tblockers="${OP_TASK_BLOCKERS[$i]}"
    tindicators="${OP_TASK_INDICATORS[$i]}"
    printf '%s' "$NEWBODY" > "$ITEM_TMP"

    set +e
    case "$kind" in
      add-gap)
        schema_call item-add --body-file "$ITEM_TMP" --section Gaps --text "$arg"
        ;;
      resolve-gap)
        CARGS=(item-close --body-file "$ITEM_TMP" --section Gaps --id "$arg" --state x)
        [ -n "$note" ] && CARGS+=(--note "$note")
        schema_call "${CARGS[@]}"
        ;;
      cancel-gap)
        CARGS=(item-close --body-file "$ITEM_TMP" --section Gaps --id "$arg" --state "~")
        [ -n "$note" ] && CARGS+=(--note "$note")
        schema_call "${CARGS[@]}"
        ;;
      add-unknown)
        schema_call item-add --body-file "$ITEM_TMP" --section Unknowns --text "$arg"
        ;;
      resolve-unknown)
        CARGS=(item-close --body-file "$ITEM_TMP" --section Unknowns --id "$arg" --state x)
        [ -n "$note" ] && CARGS+=(--note "$note")
        schema_call "${CARGS[@]}"
        ;;
      cancel-unknown)
        CARGS=(item-close --body-file "$ITEM_TMP" --section Unknowns --id "$arg" --state "~")
        [ -n "$note" ] && CARGS+=(--note "$note")
        schema_call "${CARGS[@]}"
        ;;
      add-fact)
        if [ -z "$evidence" ] || ! python3 "$EFFORT_SCHEMA" has-evidence --text "$evidence" >/dev/null 2>&1; then
          rm -f "$ITEM_TMP"
          fact_evidence_fail "$evidence" "$arg"
        fi
        schema_call item-add --body-file "$ITEM_TMP" --section Facts --text "$arg" --evidence "$evidence"
        ;;
      add-indicator)
        schema_call item-add --body-file "$ITEM_TMP" --section Indicators --text "$arg"
        ;;
      add-task)
        blockers_val="${tblockers:-none}"
        indicators_val="${tindicators:-}"
        trailing="— blockers: ${blockers_val}; indicators: ${indicators_val}"
        TARGS=(item-add --body-file "$ITEM_TMP" --section How --text "$arg" --trailing "$trailing" --current-status "$RECORD_STATUS")
        for dref in "${DEFERRED_CHILD_REFS[@]}"; do
          TARGS+=(--deferred-child "$dref")
        done
        schema_call "${TARGS[@]}"
        ;;
      check-task)
        schema_call item-close --body-file "$ITEM_TMP" --section How --id "$arg" --state x
        ;;
      cancel-task)
        CARGS=(item-close --body-file "$ITEM_TMP" --section How --id "$arg" --state "~")
        [ -n "$note" ] && CARGS+=(--note "$note")
        schema_call "${CARGS[@]}"
        ;;
    esac
    RC=$?
    set -e
    if [ "$RC" -ne 0 ]; then
      rm -f "$ITEM_TMP"
      item_op_fail
    fi
    NEWBODY="$SCHEMA_OUT"

    case "$kind" in
      add-gap|resolve-gap|cancel-gap) mark_updated gaps ;;
      add-unknown|resolve-unknown|cancel-unknown) mark_updated unknowns ;;
      add-fact) mark_updated facts ;;
      add-indicator) mark_updated indicators ;;
      add-task|check-task|cancel-task) mark_updated how ;;
    esac

    i=$((i + 1))
  done
  rm -f "$ITEM_TMP"

  FINAL_BODY="$NEWBODY"
fi

if [ -n "$STATUS" ]; then
  GATE_TMP=$(mktemp)
  printf '%s' "$FINAL_BODY" > "$GATE_TMP"
  set +e
  GATE_OUT=$(python3 "$EFFORT_SCHEMA" gate --body-file "$GATE_TMP" --from "$RECORD_STATUS" --to "$STATUS" --open-children "$GATE_OPEN_CHILDREN" --blockers "$OPEN_BLOCKERS")
  GATE_RC=$?
  set -e
  rm -f "$GATE_TMP"
  if [ "$GATE_RC" -ne 0 ]; then
    if [ "$FORCE" -eq 1 ]; then
      while IFS= read -r vline; do
        [ -n "$vline" ] && echo "warning: $vline" >&2
      done <<< "$GATE_OUT"
    else
      while IFS= read -r vline; do
        [ -n "$vline" ] && echo "$vline" >&2
      done <<< "$GATE_OUT"
      exit 1
    fi
  fi
fi

DONE_REDIRECTED=0
REDIRECT_CHILD=""
REDIRECT_PRIORITY=""
if [ "$STATUS" = "Done" ] && { [ "${GATE_RC:-1}" -eq 0 ] || [ "$FORCE" -eq 1 ]; } && [ "$OPEN_DEFERRED_CHILDREN" -gt 0 ]; then
  if [ "$HAS_BODY_CHANGES" -eq 0 ]; then
    if ! grep -q '^### ' <<< "$BODY"; then
      echo "error: $REF_CANON's body does not use the Effort schema headings (### Why, ### What, ...) — refusing to overwrite a non-schema record; migrate its body first" >&2
      exit 1
    fi
    REDIRECT_ACCOUNT_TMP=$(mktemp)
    printf '%s' "$BODY" > "$REDIRECT_ACCOUNT_TMP"
    if ! REDIRECT_ACCOUNT_MSG=$(python3 "$EFFORT_SCHEMA" account --body-file "$REDIRECT_ACCOUNT_TMP"); then
      rm -f "$REDIRECT_ACCOUNT_TMP"
      {
        echo "error: refusing to update $REF_CANON — rebuilt body would silently drop content"
        echo "$REDIRECT_ACCOUNT_MSG"
        echo "nothing was written, no fields were changed — fix $REF_CANON's body by hand first"
      } >&2
      exit 1
    fi
    rm -f "$REDIRECT_ACCOUNT_TMP"
  fi

  REDIRECT_BODY_TMP=$(mktemp)
  REDIRECT_CHILDREN_TMP=$(mktemp)
  REDIRECT_PRIORITIES_TMP=$(mktemp)
  printf '%s' "$FINAL_BODY" > "$REDIRECT_BODY_TMP"
  jq -c '[.children[] | select(.state == "open" and .status == "Deferred") | {ref, title, priority}]' <<<"$RECORD_JSON" > "$REDIRECT_CHILDREN_TMP"
  if jq -e 'any(.[]; .priority != null)' "$REDIRECT_CHILDREN_TMP" >/dev/null; then
    if ! BOARD_PRIORITIES=$(board_item_priorities); then
      rm -f "$REDIRECT_BODY_TMP" "$REDIRECT_CHILDREN_TMP" "$REDIRECT_PRIORITIES_TMP"
      echo "error: refusing to update $REF_CANON — could not read the board's Priority values to place it ahead of its deferred child; nothing was written" >&2
      exit 1
    fi
    jq -c --arg me "$ITEM_ID" '[.[] | select(.id != $me) | .priority]' <<<"$BOARD_PRIORITIES" > "$REDIRECT_PRIORITIES_TMP"
  else
    printf '[]' > "$REDIRECT_PRIORITIES_TMP"
  fi
  set +e
  REDIRECT_JSON=$(python3 "$EFFORT_SCHEMA" done-redirect --body-file "$REDIRECT_BODY_TMP" --children-file "$REDIRECT_CHILDREN_TMP" --priorities-file "$REDIRECT_PRIORITIES_TMP" 2>&1)
  REDIRECT_RC=$?
  set -e
  rm -f "$REDIRECT_BODY_TMP" "$REDIRECT_CHILDREN_TMP" "$REDIRECT_PRIORITIES_TMP"
  if [ "$REDIRECT_RC" -ne 0 ]; then
    {
      echo "error: refusing to update $REF_CANON — could not redirect Done to its deferred child:"
      printf '%s\n' "$REDIRECT_JSON"
      echo "nothing was written, no fields were changed"
    } >&2
    exit 1
  fi
  FINAL_BODY=$(jq -r '.body' <<<"$REDIRECT_JSON")
  REDIRECT_CHILD=$(jq -r '.child' <<<"$REDIRECT_JSON")
  REDIRECT_PRIORITY=$(jq -r '.priority // empty' <<<"$REDIRECT_JSON")
  if [ "$(jq -r '.gap_added' <<<"$REDIRECT_JSON")" = "true" ]; then
    mark_updated gaps
  fi
  STATUS="Planning"
  HAS_BODY_CHANGES=1
  DONE_REDIRECTED=1
fi

if [ -n "$STATUS" ]; then
  case "$STATUS" in
    Done)      gh api -X PATCH "repos/$REF_OWNER/$REF_REPO/issues/$REF_NUMBER" -f state=closed -f state_reason=completed >/dev/null ;;
    Cancelled) gh api -X PATCH "repos/$REF_OWNER/$REF_REPO/issues/$REF_NUMBER" -f state=closed -f state_reason=not_planned >/dev/null ;;
    *)         gh api -X PATCH "repos/$REF_OWNER/$REF_REPO/issues/$REF_NUMBER" -f state=open >/dev/null ;;
  esac

  set_singleselect_field "$ITEM_ID" "Status" "$STATUS"
  mark_updated status
fi

if [ -n "$ASSIGNED" ]; then
  set_singleselect_field "$ITEM_ID" "Assigned" "$ASSIGNED"
  mark_updated assigned
fi

if [ "$HAS_BODY_CHANGES" -eq 1 ]; then
  gh api -X PATCH "repos/$REF_OWNER/$REF_REPO/issues/$REF_NUMBER" -f body="$FINAL_BODY" >/dev/null
fi

if [ "$DONE_REDIRECTED" -eq 1 ] && [ -n "$REDIRECT_PRIORITY" ]; then
  set_number_field "$ITEM_ID" "Priority" "$REDIRECT_PRIORITY"
  mark_updated priority
fi

RESULTING_STATUS="${STATUS:-$RECORD_STATUS}"
if [ -n "$STATUS" ] && [ "$FORCE" -eq 1 ]; then
  DERIVED="$RESULTING_STATUS"
else
  DERIVE_TMP=$(mktemp)
  printf '%s' "$FINAL_BODY" > "$DERIVE_TMP"
  DERIVED=$(python3 "$EFFORT_SCHEMA" derive-status --body-file "$DERIVE_TMP" --current "$RESULTING_STATUS" --blockers "$OPEN_BLOCKERS")
  rm -f "$DERIVE_TMP"
fi

RATCHET_FIRED=0
FINAL_STATUS="$RESULTING_STATUS"
if [ "$DERIVED" != "${RESULTING_STATUS:-Empty}" ]; then
  RATCHET_FIRED=1
  FINAL_STATUS="$DERIVED"
  set_singleselect_field "$ITEM_ID" "Status" "$DERIVED"
  mark_updated status
fi

SHOW_RECORD=0
[ -n "$STATUS" ] && SHOW_RECORD=1
[ "$RATCHET_FIRED" -eq 1 ] && SHOW_RECORD=1

STATE_FOR_RECORD="$STATE_ORIG"
if [ -n "$STATUS" ]; then
  case "$STATUS" in
    Done|Cancelled) STATE_FOR_RECORD="closed" ;;
    *)              STATE_FOR_RECORD="open" ;;
  esac
fi

ASSIGNED_FOR_RECORD="${ASSIGNED:-$ASSIGNED_ORIG}"

if [ "${#UPDATED_FIELDS[@]}" -eq 0 ]; then
  FIELDS_JSON="[]"
else
  FIELDS_JSON=$(printf '%s\n' "${UPDATED_FIELDS[@]}" | jq -R . | jq -s .)
fi

OUT=$(jq -n --arg ref "$REF_CANON" --argjson fields "$FIELDS_JSON" '{ref: $ref, updated: $fields}')

if [ "$SHOW_RECORD" -eq 1 ]; then
  RECORD_JSON_OUT=$(jq -n \
    --arg ref "$REF_CANON" \
    --arg title "$TITLE_ORIG" \
    --arg state "$STATE_FOR_RECORD" \
    --arg status "$FINAL_STATUS" \
    --arg assigned "$ASSIGNED_FOR_RECORD" \
    --arg in_use "$IN_USE" \
    --arg parent "$PARENT_ORIG" \
    --argjson blockers "$BLOCKERS_ORIG" \
    --arg body "$FINAL_BODY" \
    --argjson open_children "$OPEN_CHILDREN" \
    --argjson open_blockers "$OPEN_BLOCKERS" \
    --arg item_id "$ITEM_ID" \
    '{ref: $ref, title: $title, state: $state, status: $status, assigned: $assigned, in_use: $in_use, parent: $parent, blockers: $blockers, body: $body, open_children: $open_children, open_blockers: $open_blockers, item_id: $item_id}')
  OUT=$(jq --argjson record "$RECORD_JSON_OUT" '. + {record: $record}' <<<"$OUT")
fi

if [ "$RATCHET_FIRED" -eq 1 ]; then
  OUT=$(jq --arg sd "${RESULTING_STATUS} -> ${DERIVED}" '. + {status_derived: $sd}' <<<"$OUT")
fi

if [ "$DONE_REDIRECTED" -eq 1 ]; then
  OUT=$(jq --arg child "$REDIRECT_CHILD" --argjson priority "${REDIRECT_PRIORITY:-null}" \
    '. + {status_derived: "Done -> Planning", deferred_redirect: {child: $child, priority: $priority}}' <<<"$OUT")
fi

SYNC_NEEDED=0
if [ -n "$PARENT_ORIG" ]; then
  if [ "$FINAL_STATUS" = "Deferred" ] && [ "$RECORD_STATUS" != "Deferred" ]; then
    SYNC_NEEDED=1
  elif [ "$RECORD_STATUS" = "Deferred" ]; then
    case "$FINAL_STATUS" in
      Deferred|Done|Cancelled) ;;
      *) SYNC_NEEDED=1 ;;
    esac
  fi
fi

SYNC_FAILED=0
if [ "$SYNC_NEEDED" -eq 1 ]; then
  SYNC_ERR_TMP=$(mktemp)
  set +e
  SYNC_OUT=$("$SCRIPT_DIR/sync-deferred-parent.sh" "$REF_CANON" 2>"$SYNC_ERR_TMP")
  SYNC_RC=$?
  set -e
  SYNC_ERR=$(cat "$SYNC_ERR_TMP")
  rm -f "$SYNC_ERR_TMP"
  [ -n "$SYNC_ERR" ] && printf '%s\n' "$SYNC_ERR" >&2
  if [ "$SYNC_RC" -eq 0 ] && jq -e 'type == "object"' <<<"$SYNC_OUT" >/dev/null 2>&1; then
    OUT=$(jq --argjson sync "$SYNC_OUT" '. + {parent_sync: $sync}' <<<"$OUT")
  else
    SYNC_FAILED=1
    echo "error: $REF_CANON itself was updated, but syncing its parent $PARENT_ORIG failed (exit $SYNC_RC) — the parent still shows the old state; re-run: sync-deferred-parent.sh $REF_CANON" >&2
    OUT=$(jq --arg parent "$PARENT_ORIG" --argjson rc "$SYNC_RC" --arg err "$SYNC_ERR" \
      '. + {parent_sync: {parent: $parent, exit_code: $rc, error: (if $err == "" then "sync-deferred-parent.sh failed without output" else $err end)}}' <<<"$OUT")
  fi
fi

printf '%s\n' "$OUT"

if [ "$SYNC_FAILED" -eq 1 ]; then
  exit 1
fi
