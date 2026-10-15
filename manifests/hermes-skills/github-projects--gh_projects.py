#!/usr/bin/env python3
"""Read and write issues in Simple Salt's simplesalt/projects repo through the GitHub
connector on the MCP proxy. The repo is fixed here, so callers never pass it."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

OWNER = "simplesalt"
REPO = "projects"
URL = os.environ.get("GITHUB_MCP_URL", "http://mcp-proxy:3003/mcp")


class McpError(Exception):
    pass


class Session:
    def __init__(self):
        self.sid = None
        self.next_id = 1
        self._post("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "gh_projects", "version": "1"}})
        self._post("notifications/initialized", {}, notify=True)

    def _post(self, method, params, notify=False):
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            body["id"] = self.next_id
            self.next_id += 1
        headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        if self.sid:
            headers["mcp-session-id"] = self.sid
        req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.sid = resp.headers.get("mcp-session-id") or self.sid
                text = resp.read().decode()
        except urllib.error.HTTPError as e:
            raise McpError(f"GitHub connector returned HTTP {e.code}: {e.read().decode()[:300]}")
        except urllib.error.URLError as e:
            raise McpError(f"cannot reach the GitHub connector at {URL}: {e.reason}")
        if notify or not text.strip():
            return None
        data = [line[5:] for line in text.splitlines() if line.startswith("data:")]
        msg = json.loads(data[-1] if data else text)
        if "error" in msg:
            raise McpError(msg["error"].get("message", json.dumps(msg["error"])))
        return msg.get("result")

    def call(self, tool, **args):
        args = {k: v for k, v in args.items() if v is not None}
        result = self._post("tools/call", {"name": tool, "arguments": args}) or {}
        parts = []
        for c in result.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "resource" and "text" in c.get("resource", {}):
                parts.append(c["resource"]["text"])
        text = "\n".join(parts)
        if result.get("isError"):
            raise McpError(text or "the GitHub tool reported an error")
        return text


def repo_args():
    return {"owner": OWNER, "repo": REPO}


def main():
    p = argparse.ArgumentParser(prog="gh_projects.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="find issues by words")
    s.add_argument("words")
    s.add_argument("--limit", type=int, default=10)

    s = sub.add_parser("list", help="list issues, newest first")
    s.add_argument("--state", choices=["open", "closed"])
    s.add_argument("--limit", type=int, default=10)

    s = sub.add_parser("read", help="read one issue")
    s.add_argument("number", type=int)

    s = sub.add_parser("comments", help="read an issue's comments")
    s.add_argument("number", type=int)

    s = sub.add_parser("comment", help="add a comment to an issue")
    s.add_argument("number", type=int)
    s.add_argument("body", help="comment text in Markdown; '-' reads it from stdin")

    s = sub.add_parser("create", help="create an issue")
    s.add_argument("--title", required=True)
    s.add_argument("--body", default="", help="'-' reads it from stdin")

    s = sub.add_parser("update", help="change an issue's title, body or state")
    s.add_argument("number", type=int)
    s.add_argument("--title")
    s.add_argument("--body", help="'-' reads it from stdin")
    s.add_argument("--state", choices=["open", "closed"])

    s = sub.add_parser("file", help="read a file or folder in the repo (read only)")
    s.add_argument("path", nargs="?", default="/")

    a = p.parse_args()
    for field in ("body",):
        if getattr(a, field, None) == "-":
            setattr(a, field, sys.stdin.read())

    try:
        gh = Session()
        if a.cmd == "search":
            out = gh.call("search_issues", query=a.words, perPage=a.limit, **repo_args())
        elif a.cmd == "list":
            out = gh.call("list_issues", state=a.state.upper() if a.state else None,
                          perPage=a.limit, orderBy="CREATED_AT", direction="DESC", **repo_args())
        elif a.cmd == "read":
            out = gh.call("issue_read", method="get", issue_number=a.number, **repo_args())
        elif a.cmd == "comments":
            out = gh.call("issue_read", method="get_comments", issue_number=a.number, **repo_args())
        elif a.cmd == "comment":
            out = gh.call("add_issue_comment", issue_number=a.number, body=a.body, **repo_args())
        elif a.cmd == "create":
            out = gh.call("issue_write", method="create", title=a.title, body=a.body, **repo_args())
        elif a.cmd == "update":
            if not (a.title or a.body or a.state):
                p.error("update needs --title, --body or --state")
            out = gh.call("issue_write", method="update", issue_number=a.number, title=a.title,
                          body=a.body, state=a.state, **repo_args())
        else:
            out = gh.call("get_file_contents", path=a.path, **repo_args())
    except McpError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
