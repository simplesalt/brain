#!/usr/bin/env python3
import json
import os
import sys

WINDOW = int(os.environ.get("BUILDER_CONTEXT_WINDOW") or 1000000)
THRESHOLD = float(os.environ.get("BUILDER_HANDOFF_PERCENT") or 75)
TAIL_BYTES = 4 * 1024 * 1024

MESSAGE = (
    "Context is at {pct:.0f}% of the window. Finish the step in hand and land it, then start "
    "no new work: write a handoff (the current state, what remains, and the key facts a fresh "
    "session needs) and ask your requester what to do next."
)


def total(usage):
    return sum(
        int(usage.get(k) or 0)
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    )


def last_tokens(transcript):
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
        if entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        if isinstance(usage, dict) and total(usage) > 0:
            return total(usage)
    return None


def used_percent(payload):
    window = payload.get("context_window")
    if isinstance(window, dict) and isinstance(window.get("used_percentage"), (int, float)):
        return float(window["used_percentage"])
    transcript = payload.get("transcript_path")
    if not transcript or not os.path.isfile(transcript):
        return None
    tokens = last_tokens(transcript)
    if not tokens:
        return None
    return 100.0 * tokens / WINDOW


def main():
    payload = json.load(sys.stdin)
    if payload.get("agent_id"):
        return
    event = payload.get("hook_event_name") or "PostToolUse"
    transcript = payload.get("transcript_path") or ""
    session = payload.get("session_id") or "unknown"
    marker_dir = os.path.join(os.environ.get("TMPDIR") or "/tmp", "builder-context-handoff")
    marker = os.path.join(marker_dir, session)
    if os.path.exists(marker):
        return
    pct = used_percent(payload)
    if pct is None or pct < THRESHOLD:
        return
    os.makedirs(marker_dir, exist_ok=True)
    with open(marker, "w") as fh:
        fh.write(transcript)
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
