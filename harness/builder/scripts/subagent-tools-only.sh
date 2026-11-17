#!/usr/bin/env python3
"""SubagentStart hook — injects the "use only what's here" instruction into every
subagent (simplesalt/projects#609).

Why this exists: the policy prompt (CLAUDE.md) reaches the top-level session but not
Explore/Plan subagents, which never see it. This hook restates the one line those
subagents still need, via additionalContext, so the constraint reaches them too.
"""
import json
import sys

MESSAGE = (
    "Use only the tools and repo already here. If you cannot succeed without "
    "additional resources, report that blocker."
)


def main():
    try:
        sys.stdin.read()
    except Exception:
        pass

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": MESSAGE,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
