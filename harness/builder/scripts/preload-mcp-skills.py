#!/usr/bin/env python3
import json
import os
import re
import sys
import urllib.request

TIMEOUT = 4
LIMIT = 9500
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand(value):
    return ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)


def parse(body, content_type):
    if "text/event-stream" in content_type:
        for line in body.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data:
                    return json.loads(data)
        return None
    return json.loads(body) if body.strip() else None


class Client:
    def __init__(self, url, headers):
        self.url = url
        self.headers = {k: expand(v) for k, v in (headers or {}).items() if isinstance(v, str)}
        self.session = None
        self.next_id = 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.next_id += 1
            msg["id"] = self.next_id
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            self.session = resp.headers.get("Mcp-Session-Id") or self.session
            body = resp.read().decode("utf-8", "replace")
            if notify:
                return None
            reply = parse(body, resp.headers.get("Content-Type", ""))
        if not isinstance(reply, dict) or "result" not in reply:
            return None
        return reply["result"]

    def skills(self):
        init = self.send("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "builder-skill-preload", "version": "1.0.0"},
        })
        if not isinstance(init, dict) or "resources" not in (init.get("capabilities") or {}):
            return []
        self.send("notifications/initialized", notify=True)
        listed = self.send("resources/list") or {}
        found = []
        for res in listed.get("resources") or []:
            uri = res.get("uri") or ""
            if not (uri.startswith("skill://") and uri.endswith("/SKILL.md")):
                continue
            read = self.send("resources/read", {"uri": uri}) or {}
            text = "".join(c.get("text") or "" for c in read.get("contents") or [])
            if text.strip():
                found.append((uri, text))
        return found


def servers(cwd):
    cfg = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~"), ".claude.json")
    try:
        with open(cfg) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    found = dict(data.get("mcpServers") or {})
    project = (data.get("projects") or {}).get(cwd) or {}
    found.update(project.get("mcpServers") or {})
    return found


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    blocks = []
    for name, spec in sorted(servers(cwd).items()):
        if not isinstance(spec, dict) or spec.get("type") not in ("http", "streamable-http") or not spec.get("url"):
            continue
        try:
            found = Client(expand(spec["url"]), spec.get("headers")).skills()
        except Exception:
            continue
        for uri, text in found:
            blocks.append(f"<skill server=\"{name}\" uri=\"{uri}\">\n{text.strip()}\n</skill>")
    if not blocks:
        return
    context = "Skills served by this session's MCP servers, preloaded at session start:\n\n" + "\n\n".join(blocks)
    if len(context) > LIMIT:
        context = context[:LIMIT] + "\n[truncated]"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
