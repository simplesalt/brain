---
name: agent-services
description: Use when building or changing an agent, an MCP server, a skill, or a service an agent uses.
---
<!-- agent-prompt -->
- Give agents access to a system through an MCP server in its own namespace.
- Host each skill on the MCP of the service that enables it. That MCP stores and processes the work, keeps its own state, and guides the agent through it.
- Keep agents stateless: they call the skill and use its result.
