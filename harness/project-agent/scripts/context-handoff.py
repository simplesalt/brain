#!/usr/bin/env python3
import json
import os
import sys

WINDOW = int(os.environ.get("PROJECT_AGENT_CONTEXT_WINDOW") or 1000000)
THRESHOLD = float(os.environ.get("PROJECT_AGENT_HANDOFF_PERCENT") or 75)
TAIL_BYTES = 4 * 1024 * 1024
STATE_DIR = os.path.join(os.environ.get("TMPDIR") or "/tmp", "project-agent-context")

MESSAGE = (
    "Context is at {pct:.0f}% of the window and this session never compacts. Take on no new work. "
    "Finish the step in hand, then write a handoff into the Effort you hold: current state, what "
    "remains, key facts, and the address of every open dispatched session. Release every Effort "
    "you hold, then tell the person where things stand in a few lines and ask what to do next."
)


def last_usage(transcript):
    with open(transcript, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - TAIL_BYTES))
        lines = fh.read().splitlines()
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        if isinstance(usage, dict):
            return usage
    return None


def used_percent(payload):
    transcript = payload.get("transcript_path")
    if not transcript or not os.path.isfile(transcript):
        return None
    usage = last_usage(transcript)
    if not usage:
        return None
    tokens = sum(
        int(usage.get(k) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    )
    return 100.0 * tokens / WINDOW


def main():
    payload = json.load(sys.stdin)
    if payload.get("agent_id"):
        return
    event = payload.get("hook_event_name") or "PostToolUse"
    session = payload.get("session_id") or "unknown"
    marker = os.path.join(STATE_DIR, session)
    if os.path.exists(marker):
        return
    pct = used_percent(payload)
    if pct is None or pct < THRESHOLD:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(marker, "w") as fh:
        fh.write(str(pct))
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": MESSAGE.format(pct=pct),
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
