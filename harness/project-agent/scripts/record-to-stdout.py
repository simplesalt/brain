#!/usr/bin/env python3
import datetime
import json
import os
import socket
import sys

RECORDING_SKILLS = {
    "report-problem": "the whole record as args: first line `<problem|opportunity|unexpected-outcome|tracked-result>: <title>`, then up to about 10 lines of evidence, sequence and conclusion",
    "capture-feedback": "one observation as args: first line `<problem|opportunity|unexpected-outcome|tracked-result>: <title>`, then 3-10 lines naming the piece of the system responsible and the proposed fix",
}
POD_STDOUT = os.environ.get("AGENT_REPORT_STDOUT") or "/proc/1/fd/1"


def skill_name(raw):
    return raw.rsplit(":", 1)[-1].lstrip("/")


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def main():
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Skill":
        return
    tool_input = payload.get("tool_input") or {}
    skill = skill_name(str(tool_input.get("skill") or ""))
    if skill not in RECORDING_SKILLS:
        return
    content = str(tool_input.get("args") or "").strip()
    if not content:
        deny("Nothing recorded: invoke /" + skill + " again with " + RECORDING_SKILLS[skill] + ".")
        return
    session_id = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    host = os.environ.get("HOSTNAME") or socket.gethostname()
    record = {
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "level": "error",
        "logger": "agent-report",
        "skill": skill,
        "session": host + "/" + session_id,
        "agent": payload.get("agent_type") or "",
        "bank": os.environ.get("HINDSIGHT_BANK_ID") or "",
        "cwd": payload.get("cwd") or "",
        "msg": content,
    }
    try:
        with open(POD_STDOUT, "a") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as err:
        deny("Not recorded: writing to the pod stdout failed (" + str(err) + "). Report this to your upstream with the record text.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
