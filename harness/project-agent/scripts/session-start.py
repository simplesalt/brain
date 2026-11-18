#!/usr/bin/env python3
import json
import os
import socket
import sys
import time

HOME = os.path.expanduser("~")
SESSIONS_DIR = os.environ.get("PROJECT_AGENT_SESSIONS_DIR") or os.path.join(HOME, ".claude", "sessions")
SYNCED_DIR = os.environ.get("PROJECT_AGENT_SYNCED_SKILLS_DIR") or os.path.join(HOME, ".claude", "skills", "synced")
STATE_DIR = os.path.join(os.environ.get("TMPDIR") or "/tmp", "project-agent-session")
SYNC_WAIT = float(os.environ.get("PROJECT_AGENT_SKILL_SYNC_WAIT") or 15)
SYNC_SETTLE = 2.0


def bridge_address(session_id):
    try:
        names = os.listdir(SESSIONS_DIR)
    except OSError:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS_DIR, name)) as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("sessionId") != session_id:
            continue
        bridge = record.get("bridgeSessionId")
        if isinstance(bridge, str) and bridge:
            return "bridge:" + bridge
    return None


def synced_snapshot():
    found = []
    for root, _, files in os.walk(SYNCED_DIR):
        for name in files:
            path = os.path.join(root, name)
            try:
                found.append((path, os.stat(path).st_mtime_ns))
            except OSError:
                continue
    return sorted(found)


def wait_for_skill_sync():
    deadline = time.monotonic() + SYNC_WAIT
    last = synced_snapshot()
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.5)
        now = synced_snapshot()
        if now != last:
            last = now
            stable_since = time.monotonic()
        elif last and time.monotonic() - stable_since >= SYNC_SETTLE:
            return


def marker(session_id):
    return os.path.join(STATE_DIR, session_id + ".bridge")


def identity_lines(session_id, bridge):
    host = os.environ.get("HOSTNAME") or socket.gethostname()
    lines = ["Your session label: " + host + "/" + session_id]
    if bridge:
        lines.append("Your bridge address: " + bridge)
    else:
        lines.append("Your bridge address is not known yet; it will be given with a later message.")
    bank = os.environ.get("HINDSIGHT_BANK_ID")
    if bank:
        lines.append("Your memory bank: " + bank)
    return lines


def remember_bridge(session_id, bridge):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(marker(session_id), "w") as fh:
        fh.write(bridge)


def on_session_start(payload, session_id):
    wait_for_skill_sync()
    bridge = bridge_address(session_id)
    if bridge:
        remember_bridge(session_id, bridge)
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(identity_lines(session_id, bridge)),
            "reloadSkills": True,
        }
    }


def on_prompt(payload, session_id):
    if os.path.exists(marker(session_id)):
        return None
    bridge = bridge_address(session_id)
    if not bridge:
        return None
    remember_bridge(session_id, bridge)
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "Your bridge address: " + bridge,
        }
    }


def main():
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        return
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        out = on_session_start(payload, session_id)
    elif event == "UserPromptSubmit":
        out = on_prompt(payload, session_id)
    else:
        out = None
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
