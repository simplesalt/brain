#!/usr/bin/env python3
import datetime
import json
import os
import re
import socket
import sys

POD_STDOUT = os.environ.get("AGENT_REPORT_STDOUT") or "/proc/1/fd/1"
SID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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
    if skill_name(str((payload.get("tool_input") or {}).get("skill") or "")) != "close":
        return
    session_id = str(payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or "")
    if not SID.match(session_id):
        deny("Not closed: this session has no usable session id. Tell your requester the session could not close itself.")
        return
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    record = {
        "session_id": session_id,
        "bridge_session_id": os.environ.get("CLAUDE_CODE_BRIDGE_SESSION_ID") or "",
        "cwd": payload.get("cwd") or "",
        "transcript_path": payload.get("transcript_path") or "",
        "time": now,
    }
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    folder = os.path.join(config, "resolved-sessions.d")
    try:
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, f".{session_id}.{os.getpid()}")
        with open(tmp, "w") as fh:
            fh.write(json.dumps(record) + "\n")
        os.replace(tmp, os.path.join(folder, session_id))
    except OSError as err:
        deny("Not closed: writing the close marker failed (" + str(err) + "). Tell your requester the session could not close itself.")
        return
    line = {
        "time": now,
        "level": "info",
        "logger": "session-close",
        "session": (os.environ.get("HOSTNAME") or socket.gethostname()) + "/" + session_id,
        "bridge_session_id": record["bridge_session_id"],
        "bank": os.environ.get("HINDSIGHT_BANK_ID") or "",
        "cwd": record["cwd"],
        "msg": "session closed itself",
    }
    try:
        with open(POD_STDOUT, "a") as out:
            out.write(json.dumps(line) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
