#!/usr/bin/env python3
import datetime
import json
import os
import re
import socket
import sys
import tempfile
import time
import uuid

IDLE_SECONDS = int(os.environ.get("BUILDER_IDLE_CHECK_IN_SECONDS") or 1200)
STATE_DIR = os.path.join(tempfile.gettempdir(), "idle-check-in")
POD_STDOUT = os.environ.get("AGENT_REPORT_STDOUT") or "/proc/1/fd/1"
TAG = "[idle check-in]"
MESSAGE = (
    f"{TAG} This session has been idle about {max(1, round(IDLE_SECONDS / 60))} minutes. "
    "Are you done? If so, make sure your requester has your final report (sent with SendMessage), then end. "
    "If you're blocked, tell your requester with SendMessage what blocks you, naming the blocking Effort, then end. "
    "Do not wait."
)


def paths(session_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:128]
    return os.path.join(STATE_DIR, f"{safe}.turn"), os.path.join(STATE_DIR, f"{safe}.checked")


def write(path, text):
    tmp = f"{path}.{os.getpid()}"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def log_check_in(event):
    record = {
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "level": "info",
        "logger": "idle-check-in",
        "session": (os.environ.get("HOSTNAME") or socket.gethostname()) + "/" + str(event.get("session_id") or ""),
        "bank": os.environ.get("HINDSIGHT_BANK_ID") or "",
        "idle_seconds": IDLE_SECONDS,
        "msg": "idle check-in sent",
    }
    try:
        with open(POD_STDOUT, "a") as out:
            out.write(json.dumps(record) + "\n")
    except OSError:
        pass


def on_prompt(event, turn, checked):
    write(turn, uuid.uuid4().hex)
    if TAG not in str(event.get("prompt") or ""):
        try:
            os.remove(checked)
        except FileNotFoundError:
            pass
    return 0


def on_stop(event, turn, checked):
    if event.get("stop_hook_active") or os.path.exists(checked):
        return 0
    token = uuid.uuid4().hex
    write(turn, token)
    time.sleep(IDLE_SECONDS)
    if read(turn) != token or os.path.exists(checked):
        return 0
    write(checked, token)
    log_check_in(event)
    print(MESSAGE, file=sys.stderr)
    return 2


def main():
    try:
        event = json.load(sys.stdin)
        session_id = str(event.get("session_id") or "")
        if not session_id:
            return 0
        os.makedirs(STATE_DIR, exist_ok=True)
        turn, checked = paths(session_id)
        if event.get("hook_event_name") == "UserPromptSubmit":
            return on_prompt(event, turn, checked)
        if event.get("hook_event_name") == "Stop":
            return on_stop(event, turn, checked)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
