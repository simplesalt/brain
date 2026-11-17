#!/usr/bin/env python3
"""SessionEnd hook — releases every Effort this session still holds (via the cluster
Effort MCP server), then removes the session's register file. Fail open."""
import json
import os
import re
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TOTAL_BUDGET = 8.0
REF_PATTERN = re.compile(r"^(?:[\w.-]+/[\w.-]+)?#(\d+)$")


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read())
    except Exception:
        payload = None
    sid = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(sid, str) or not sid:
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not sid or sid in (".", "..") or "/" in sid or "\0" in sid:
        return
    path = os.path.join(os.path.expanduser("~"), ".claude", "selected-efforts.d", sid)
    try:
        try:
            with open(path) as handle:
                refs = [line.strip() for line in handle]
        except OSError:
            refs = []
        refs = [r for r in refs if REF_PATTERN.match(r)]
        if refs:
            from effort_mcp_client import call_tool
            session = socket.gethostname() + "/" + sid
            deadline = time.monotonic() + TOTAL_BUDGET
            for ref in refs:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                call_tool("effort_release", {"ref": ref, "session": session}, remaining)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
