#!/usr/bin/env python3
"""PostToolUse hook — keeps ~/.claude/selected-efforts.d/<session_id> in step with
mcp__efforts__effort_select / effort_release calls this session made for itself."""
import json
import os
import re
import socket
import sys

REF_PATTERN = re.compile(r"^(?:[\w.-]+/[\w.-]+)?#(\d+)$")


def parse_response(response):
    """Return (is_error, parsed_json_or_None) from any tool_response shape."""
    if isinstance(response, dict) and "content" in response:
        if response.get("isError"):
            return True, None
        return parse_response(response["content"])
    if isinstance(response, dict):
        if response.get("isError") or response.get("is_error"):
            return True, None
        return False, response
    if isinstance(response, list):
        for block in response:
            if isinstance(block, dict) and block.get("type") == "text":
                return parse_response(block.get("text"))
        return True, None
    if isinstance(response, str):
        try:
            data = json.loads(response)
        except ValueError:
            return True, None
        return (False, data) if isinstance(data, dict) else (True, None)
    return True, None


def main():
    payload = json.loads(sys.stdin.buffer.read())
    if not isinstance(payload, dict):
        return
    sid = payload.get("session_id")
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(sid, str) or not sid or sid in (".", "..") or "/" in sid or "\0" in sid:
        return
    if not isinstance(tool_input, dict):
        return
    if tool_input.get("session") != socket.gethostname() + "/" + sid:
        return
    if tool == "mcp__efforts__effort_select":
        add = True
    elif tool == "mcp__efforts__effort_release":
        add = False
    else:
        return
    is_error, data = parse_response(payload.get("tool_response"))
    if is_error:
        return
    ref = data.get("ref") if isinstance(data, dict) else None
    if not isinstance(ref, str) or not REF_PATTERN.match(ref):
        ref = tool_input.get("ref")
    if not isinstance(ref, str) or not REF_PATTERN.match(ref):
        return

    directory = os.path.join(os.path.expanduser("~"), ".claude", "selected-efforts.d")
    path = os.path.join(directory, sid)
    try:
        with open(path) as handle:
            refs = [line.strip() for line in handle if line.strip()]
    except OSError:
        refs = []
    refs = [r for r in refs if r != ref]
    if add:
        refs.append(ref)
    if not refs:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.makedirs(directory, exist_ok=True)
    with open(path, "w") as handle:
        handle.write("".join(r + "\n" for r in refs))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
