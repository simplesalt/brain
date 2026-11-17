#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys

SID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
WORKTREE_PATTERN = re.compile(r"^((.+)/\.claude/worktrees/bridge-[^/]+)(/.*)?$")
RESOLVE_TOOL = "mcp__hearthbot__set_thread_resolved"
KEEP_REASONS = {"clear", "resume", "compact"}


def remove(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except OSError:
        pass


def marker_path(claude, sid):
    return os.path.join(claude, "resolved-sessions.d", sid)


def record_resolution(claude, sid, payload):
    tool_input = payload.get("tool_input")
    if payload.get("tool_name") != RESOLVE_TOOL or not isinstance(tool_input, dict):
        return
    resolved = tool_input.get("resolved")
    marker = marker_path(claude, sid)
    if resolved is True:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "a").close()
    elif resolved is False:
        remove(marker)


def remove_worktree(cwd):
    match = WORKTREE_PATTERN.match(os.path.realpath(cwd))
    if not match:
        return
    worktree, repo = match.group(1), match.group(2)
    os.chdir(repo)
    subprocess.run(
        ["git", "-C", repo, "worktree", "remove", "--force", "--force", worktree],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
    )


def remove_session(claude, sid, payload):
    marker = marker_path(claude, sid)
    if not os.path.exists(marker) or payload.get("reason") in KEEP_REASONS:
        return
    projects = os.path.join(claude, "projects")
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and os.path.dirname(os.path.dirname(transcript)) == projects \
            and os.path.basename(transcript) == sid + ".jsonl":
        project = os.path.dirname(transcript)
        remove(transcript)
        remove(os.path.join(project, sid))
        try:
            os.rmdir(project)
        except OSError:
            pass
    remove(os.path.join(claude, "file-history", sid))
    remove(os.path.join(claude, "session-env", sid))
    cwd = payload.get("cwd")
    if isinstance(cwd, str):
        remove_worktree(cwd)
    remove(marker)


def main():
    payload = json.loads(sys.stdin.buffer.read())
    if not isinstance(payload, dict):
        return
    sid = payload.get("session_id")
    if not isinstance(sid, str) or not SID_PATTERN.match(sid):
        return
    claude = os.path.join(os.path.expanduser("~"), ".claude")
    event = payload.get("hook_event_name")
    if event == "PostToolUse":
        record_resolution(claude, sid, payload)
    elif event == "SessionEnd":
        remove_session(claude, sid, payload)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
