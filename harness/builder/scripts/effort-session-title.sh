#!/usr/bin/env python3
"""SessionStart / UserPromptSubmit hook — titles the session "#<N> <name> · <Status>"
after the Effort it most recently selected and still holds (simplesalt/projects#630).

Why this exists: a coding session is otherwise anonymous in the session list. It reads
only this session's register, so a session holding nothing makes no board call, and it
never shows an Effort whose In Use holder is another session.
"""
import json
import math
import os
import re
import socket
import sys
import time

DEFAULT_TIMEOUT = 8.0
TIMEOUT_ENV = "EFFORT_SESSION_TITLE_TIMEOUT"
REF_PATTERN = re.compile(r"^(?:[\w.-]+/[\w.-]+)?#(\d+)$")
TYPE_PREFIX = re.compile(r"^\[[^\[\]\s]+\]\s+")
SEPARATOR = " · "


def budget_seconds():
    raw = os.environ.get(TIMEOUT_ENV, "")
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_TIMEOUT
    return value


def held_refs(session_id):
    path = os.path.join(os.path.expanduser("~"), ".claude", "selected-efforts.d", session_id)
    try:
        with open(path) as handle:
            lines = [line.strip() for line in handle]
    except OSError:
        return []
    return [line for line in lines if REF_PATTERN.match(line)]


def build_title(data, fallback_ref):
    status = data.get("status")
    name = data.get("title")
    if not isinstance(status, str) or not status.strip():
        return None
    if not isinstance(name, str):
        return None
    ref = data.get("ref")
    if not isinstance(ref, str) or "#" not in ref:
        ref = fallback_ref
    number = ref.rsplit("#", 1)[1]
    if not number.isdigit():
        return None
    name = TYPE_PREFIX.sub("", name.strip(), count=1).strip()
    if not name:
        return None
    return "#" + number + " " + name + SEPARATOR + status.strip()


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from effort_mcp_client import call_tool

    payload = json.loads(sys.stdin.buffer.read())
    if not isinstance(payload, dict):
        return
    session_id = payload.get("session_id")
    event = payload.get("hook_event_name")
    if not isinstance(session_id, str) or not session_id:
        return
    if not isinstance(event, str) or not event:
        return
    if session_id in (".", "..") or "/" in session_id or "\0" in session_id:
        return

    refs = held_refs(session_id)
    if not refs:
        return

    holder = socket.gethostname() + "/" + session_id
    deadline = time.monotonic() + budget_seconds()

    for ref in reversed(refs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        data = call_tool("effort_get", {"ref": ref}, remaining)
        if not isinstance(data, dict):
            return
        if data.get("in_use") != holder:
            continue
        title = build_title(data, ref)
        if title is None:
            return
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": event,
                "sessionTitle": title,
            }
        }))
        return


if __name__ == "__main__":
    try:
        main()
        sys.stdout.flush()
    except Exception:
        pass
    sys.exit(0)
