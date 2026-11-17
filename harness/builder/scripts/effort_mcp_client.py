"""Tiny stdlib client for the cluster Effort MCP server (stateless Streamable HTTP)."""
import json
import os
import urllib.request

DEFAULT_URL = "http://efforts.ssint-main-efforts.svc:8080/mcp/coordinator"


def call_tool(name, arguments, timeout):
    """Return the parsed JSON of the first text block, or None on any error."""
    try:
        url = os.environ.get("EFFORT_MCP_URL") or DEFAULT_URL
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }).encode()
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
        )
        with urllib.request.urlopen(request, timeout=max(float(timeout), 0.05)) as response:
            message = json.loads(response.read())
        result = message.get("result")
        if not isinstance(result, dict) or result.get("isError"):
            return None
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                return json.loads(block.get("text"))
        return None
    except Exception:
        return None
