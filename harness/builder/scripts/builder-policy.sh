#!/bin/sh
set -u

POLICY_DIR=/etc/claude-code/policy
BANK="${HINDSIGHT_BANK_ID:-}"

if [ "${STATELESS:-TRUE}" = "FALSE" ]; then
    out="$(jq -n --slurpfile s "${POLICY_DIR}/common.json" \
        --arg model  "${BSB_MODEL:-}" \
        --arg theme  "${BSB_THEME:-}" \
        --arg effort "${BSB_THINKING_EFFORT:-}" \
        --arg think  "${BSB_ALWAYS_THINKING:-}" \
        --arg window "${BSB_AUTO_COMPACT_WINDOW:-}" \
        --arg memory "${BSB_AUTO_MEMORY:-}" \
        '
        def str($k; $v):
            if   $v == ""      then .
            elif $v == "unset" then del(.[$k])
            else .[$k] = $v end;
        def bool($k; $v):
            if   $v == ""      then .
            elif $v == "unset" then del(.[$k])
            elif ($v | ascii_downcase) == "true"  then .[$k] = true
            elif ($v | ascii_downcase) == "false" then .[$k] = false
            else . end;
        def num($k; $v):
            if   $v == ""      then .
            elif $v == "unset" then del(.[$k])
            elif ($v | test("^[0-9]+$")) then .[$k] = ($v | tonumber)
            else . end;
        {managedSettings: ($s[0]
            | str("model";                 $model)
            | str("theme";                 $theme)
            | str("effortLevel";           $effort)
            | bool("alwaysThinkingEnabled"; $think)
            | num("autoCompactWindow";     $window)
            | bool("autoMemoryEnabled";    $memory)
            | .env.CLAUDE_CODE_DISABLE_POLICY_SKILLS = "1")}
        ' 2>/dev/null)" || out=''
    [ -n "${out}" ] || out='{}'
    printf '%s\n' "${out}"
    exit 0
fi

servers="${AGENT_MCP_SERVERS:-}"
names="$(printf '%s' "${servers}" | jq -c 'if type == "object" then keys else [] end' 2>/dev/null)" || names='[]'
[ -n "${names}" ] || names='[]'

out="$(jq -n --slurpfile s "${POLICY_DIR}/builder.json" \
    --arg bank "${BANK}" \
    --arg role "${AGENT_ROLE:-}" \
    --arg mission "${AGENT_MISSION:-}" \
    --arg scope "${AGENT_SCOPE:-}" \
    --argjson granted "${names}" \
    '
    def section($title; $text):
        if $text == "" then "" else "\n## " + $title + "\n\n" + ($text | rtrimstr("\n")) + "\n" end;
    {managedSettings: ($s[0]
        | .deniedMcpServers |= map(select(.serverName as $n | $granted | index($n) | not))),
     claudeMd: ("<!-- agent-prompt -->\n\nYou are the "
        + (if $bank == "" then "unassigned" else $bank end)
        + " builder.\n"
        + section("Role"; $role)
        + section("Mission"; $mission)
        + section("Scope"; $scope))}
    ' 2>/dev/null)" || out=''

[ -n "${out}" ] || out='{}'
printf '%s\n' "${out}"
