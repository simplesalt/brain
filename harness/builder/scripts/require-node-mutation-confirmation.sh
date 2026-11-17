#!/usr/bin/env python3
"""PreToolUse hook — asks for explicit operator confirmation before a `flux` MCP
call that directly mutates a Kubernetes `Node` object (simplesalt/projects#241,
action class 2: "cycling a node containing [a builder-rc pod]").

This hook covers the class the operator called out: directly
restarting/replacing the underlying k8s node that hosts a `claude-rc-*` pod —
which destroys that pod's Claude Code session's uncommitted mid-turn state,
through a non-git tool surface.

Per CLAUDE.md, "there is no local kubectl in this image" — the `flux` MCP
server is the only cluster-mutation path available to a session here. Its
tool surface (per fluxoperator.dev/docs/mcp/tools/) is generic, not
Node-specific:
  - `apply_kubernetes_manifest(yaml_content, overwrite=False)` — server-side
    applies arbitrary multi-doc YAML. This is also how a Node would be
    cordoned (`spec.unschedulable: true`) — there is no dedicated cordon/
    drain tool, so cordoning necessarily goes through this one.
  - `delete_kubernetes_resource(apiVersion, kind, name, namespace)` — deletes
    one resource by coordinates. Deleting a Node object this way removes it
    from the API server's registry outright.

What this hook mechanizes: PreToolUse hook matchers CAN reach MCP tool calls
(matcher syntax `mcp__<server>__<tool>` / `mcp__<server>__.*`, confirmed
against Claude Code's hooks reference — this is NOT the same "hook matchers
may not reach it" situation as e.g. a raw HTTP call from Bash). Both tools
above have a *structured* tool_input (not free text), so detecting "this
call touches a Node" is deterministic, not a heuristic regex over a shell
string:
  - `delete_kubernetes_resource`: ask if `kind` (case-sensitive, as k8s kind
    names are) equals `Node`.
  - `apply_kubernetes_manifest`: split `yaml_content` on `---` document
    separators and ask if any document has a top-level `kind: Node` line.

What this hook deliberately does NOT and CANNOT cover — read before assuming
node-cycling is fully gated:
  - It cannot tell whether the targeted Node actually hosts a claude-rc-*
    pod right now. Confirming that would require a live cluster read (which
    Node is each claude-rc-* pod currently scheduled on), and this hook is a
    plain local script with no cluster credentials or API access of its own
    — the `flux` MCP server is only reachable from inside the running Claude
    Code session's tool-call loop, not from a PreToolUse hook subprocess.
    So this asks on EVERY direct Node apply/delete, cluster-wide, not just
    ones that would affect a builder-rc pod. Deliberately over-inclusive
    rather than silently under-inclusive.
  - It does NOT cover draining a node by deleting/evicting its pods one by
    one. Each individual `delete_kubernetes_resource(kind=Pod, ...)` call is
    indistinguishable, from this hook's vantage, from any ordinary,
    completely benign single-pod restart — there is no `kind: Node` anywhere
    in that call's tool_input. Gating every Pod delete to catch the rare
    node-drain-by-many-pod-deletes sequence would make routine pod restarts
    require confirmation too, which defeats the #240 posture for no real
    safety gain. Accepted gap, not fixed here.
  - It does NOT and CANNOT cover an actual physical/VM reboot of the
    underlying node — no tool available in this sandbox reaches that layer
    (no SSH, no IPMI, no cloud provider API). Only the k8s-API-level actions
    reachable through the two flux MCP tools above are in scope.

Fail-open, always, same posture as the rest of this directory: any missing/
malformed field, unexpected tool_input shape, or unhandled exception is
logged to stderr and treated as "nothing detected" (exit 0 / allow) — a
detection bug must never silently block an unrelated cluster operation.
"""
import json
import re
import sys

NODE_KIND_RE = re.compile(r'^\s*kind:\s*[\'"]?Node[\'"]?\s*(#.*)?$', re.MULTILINE)


def log(msg):
    print(f"require-node-mutation-confirmation: {msg}", file=sys.stderr)


def ask(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


NODE_ASK_REASON = (
    "This `flux` MCP call directly {verb} a Kubernetes Node object{detail}. "
    "Deleting, cordoning, or otherwise mutating a node cycles whatever "
    "claude-rc-* builder pods it currently hosts (strategy: Recreate) — each "
    "such pod hosts a live Claude Code session, possibly a different "
    "concurrent one from this one, and cycling it destroys that session's "
    "uncommitted mid-turn state, exactly like a builder-rc digest bump does "
    "but through a non-git path. This hook cannot check whether this "
    "specific node currently hosts a claude-rc-* pod (no cluster-read access "
    "from a local hook), so it asks on every direct Node mutation. Confirm "
    "this is deliberate and, if it targets a node with an active builder-rc "
    "session, operator-approved. See simplesalt/projects#241."
)


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return

    try:
        if tool_name == "mcp__flux__delete_kubernetes_resource":
            kind = tool_input.get("kind")
            if kind == "Node":
                name = tool_input.get("name", "<unknown>")
                ask(NODE_ASK_REASON.format(
                    verb="deletes", detail=f" (name: {name})"
                ))
            return

        if tool_name == "mcp__flux__apply_kubernetes_manifest":
            yaml_content = tool_input.get("yaml_content")
            if not isinstance(yaml_content, str) or not yaml_content.strip():
                return
            for doc in yaml_content.split("\n---"):
                if NODE_KIND_RE.search(doc):
                    ask(NODE_ASK_REASON.format(
                        verb="applies a manifest containing", detail=""
                    ))
            return
    except SystemExit:
        raise
    except Exception as exc:
        log(f"detection failed, allowing unconfirmed (fail open): {exc}")
        return


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"unexpected failure, allowing unconfirmed (fail open): {exc}")
    sys.exit(0)
