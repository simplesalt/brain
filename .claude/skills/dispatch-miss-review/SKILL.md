---
name: dispatch-miss-review
description: Use for the weekly review of dispatch routing — reads the last seven days of dispatch records, finds misroutes, scores them and files them under failure patterns in the Notion "Dispatch misses" tally. Also use when asked to run that review by hand.
---

# Weekly dispatch miss review

Goal: every misroute by the dispatch MCP over one week lands in Notion, filed under a failure pattern, scored, so the patterns with the highest importance get fixed first and a fix can show a measurable drop.

Arguments: an optional UTC end date `YYYY-MM-DD`. Default: yesterday (UTC). The week is the seven days ending on that date.

## Where things are

- Router: `scripts/dispatch_mcp.py` and `manifests/dispatch-scopes.json` in simplesalt/brain. The live router prompt is at `GET http://dispatch.ssint-main-coding:8080/prompt`. Read both before judging a route.
- Records: every dispatch call and every misroute report writes one JSON line with `"level": "audit"` to the dispatch container's stdout. The cluster log store keeps them; read them through the logs MCP (`http://logs-mcp.cluster-main-observability:8080/mcp`, tool `entries`) with `namespace=eq.ssint-main-coding`, `container=eq.dispatch`, `message=like.*"level": "audit"*`, `at=gte.<start>` and `at=lt.<end>`, ordered by `at`. Records are complete from 2026-11-19; earlier weeks hold only refused calls.
  - `event: dispatch` fields: `ts`, `caller`, `slug`, `hops`, `text`, `pod` (the agent it went to), `layer`, `target`, `route` (`table` or `fallback`, with `route_reason`), `model_answer`, `reasoning`, `served_model`, `provider`, `status` (the agent endpoint's HTTP status), `address` (the started session) and `log`.
  - `event: misroute` fields: `ts`, `address` (the session that received the work), `right_agent`, `reason`. Match it to its dispatch by `address`.
- Tally (Notion, Team Dashboard › Dispatch misses), built like the Hermes misses tally:
  - Failure patterns: Pattern (title), Category, Description, Status, Fix effort, Fix shipped, First seen. Computed columns are never written.
  - Misses: Miss (title), Pattern (relation), Date, Impact, Cost, What the router reasoned, Should have, Dispatch (`<ts> — <slug> — <caller>`), Review run.

## Steps

1. Load the week's audit records (none, or the log store down: say so and stop). Drop calls with `hops` above the hop limit that a person or a test sent on purpose (status 403 with a `route-test` style slug).
2. Find misroutes. Each of these is a miss:
   - a `misroute` record from the receiving agent (mark the Miss title "[receiver report]");
   - the same caller sends the same work again within a day with a different `pod`, after a session started on the first;
   - `route` is `fallback` (no model answer, no table row, or a model error);
   - reading `text` against the router prompt and the domain boundaries in the registry, a different agent clearly owns the work. Judge what must be true in the request, not the words it uses.
   A request that is over-prescriptive (names files, formats, registries or steps outside the caller's own domain) and therefore lands on the agent its wording names is a caller miss, not a router miss: file it under the pattern "Caller over-prescribes the request" with the caller in Dispatch. Do not count an agent endpoint failure (status 5xx) that the router could not have avoided; do not count the same work twice.
3. Root-cause each router miss from `reasoning`: quote the step where it went wrong (for example "publishing OCI layers to ghcr. That is target image"), and say which question, definition, table row or domain line it misread or lacked.
4. Score each miss: Impact 1-5 (1 caught at once by the receiver … 5 work done in the wrong repo or lost) and Cost 1-5 (1 no rework … 5 hours of rework or a person had to step in).
5. Match each miss to a pattern. Fetch all Failure patterns rows first. A pattern is a cause ("Reads an OCI layer as an image", "Treats a desired-state request as planning"), not an incident. Create a new pattern only when none fits, with Status Open and First seen = the dispatch date. If two existing patterns are one, say so in the summary rather than merging them.
6. Before writing, query Misses for rows with the same Dispatch to avoid filing twice when a week is rerun. Then create one Misses row per miss.
7. If a pattern has Status "Fixed - watching" or "Resolved" and gets a new miss, keep its Status but mention it in the summary.

## Report

End with a short summary in plain words for Dylan: how many dispatches were reviewed and how many went to each agent, how many misses were filed and under how many patterns (how many new), how many were caller misses, the top three patterns by score this week (sum of Impact × Cost), and any pattern with a shipped fix that recurred. Link the Dispatch misses page. Propose the smallest router change for the top router pattern; never change the router or the registry from this review.
