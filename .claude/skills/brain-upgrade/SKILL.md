---
name: brain-upgrade
description: Use when moving the gbrain, hermes-agent or hindsight pin in simplesalt/brain (a planned step, a scheduled pin walk, a rollback), or re-checking Hermes's skill allow-list.
---

<!-- agent-prompt -->

## What it carries

- `.claude/skills/brain-upgrade/upgrade-path.yaml`: the stepwise plan (rules, order, stops with before/validate/rollback, skip ranges, bisect fallbacks) for the gbrain, hermes-agent and hindsight pins.
- `scripts/skill-diff.py` in the same folder: tells whether a pin move adds, removes or changes skills.
- The Hermes skill review below, run in every gbrain and hermes-agent stop.

Every file the plan names (`manifests/ai.yaml`, `manifests/hermes-skills.yaml`, `manifests/gbrain-rehearsal.yaml`, ...) is in simplesalt/brain. Only the brain workstream's agent (ssint-build-brain) changes that repo; any other agent asks it through an Effort assigned to ssint-build-brain.

## Walk one step

1. Read `rules` and `order` first; they bind every step.
2. Read the live pin from `pinned_in` in simplesalt/brain, never from `current` (the pin when the plan was last revised). Every place in `pinned_in` must agree.
3. Take the first stop after the live pin. Never skip a stop or land in a `skip` range, never jump to `target`, never use a floating tag.
4. Check the service's `hold` and the stop's `prerequisites` (below), then satisfy `before_first_stop` (once), the stop's `before`, and any `order` constraint on it. Anything unmet: stop and report it, do not bump.
5. gbrain or hermes-agent stop (hindsight stops have none): run its `skill_review` (below) before the bump commit.
6. gbrain stop: once the rehearsal copy runs the new SHA (skill review step 3), run the page history check (below) there, before the bump commit. Any finding: stop the upgrade, do not bump, and report it quoting the lines found.
7. One commit moves every `pinned_in` place to the stop's pin (a gbrain stop also moves the skills sync to the same SHA), together with `manifests/hermes-skills.yaml` as approved (unchanged if nothing was). Push: pushing deploys.
8. Run every `validate` check. A check fails: apply the stop's `rollback`. A `bisect` entry is a fallback only when the hop fails, and a "restored copy only" entry never goes to prod.
9. Report the outcome for the upgrade Effort (simplesalt/projects#528; its planner records it), not in the plan: skill-diff result and exit code, verdicts, the user's decision, each `before` and `validate` result, any commit landed, the new live pin.

## Holds and prerequisites

- A service with a `hold` whose `stop` is its next stop is held. Before `recheck_from`: report the hold and do not move that service. From `recheck_from`: check `release_when`; met: report the evidence and propose the change to the plan (below); unmet: report what is still missing and do not move it.
- A prerequisite with `require: done` holds the stop until `effort_get` on its `effort` shows status Done. Any other prerequisite is the condition its `what` names, checked directly; its `effort` says where it is tracked.
- A held service reports its hold and the run moves to the next service in `services` order.

## Scheduled runs

A scheduled run walks with nobody watching. It asks dylan only when a Hermes skill change needs approval; everything else it decides from this skill and the plan, or stops and reports.

- One stop per run: take the services in `services` order and walk the first one whose next stop is not held. Never skip a stop or a `before` item.
- When the review proposes a change, send the proposal to dylan and stop without bumping until they answer. The next run resumes from the same live pin.
- A failed `before` or `validate` check: apply the stop's `rollback` to whatever this run already changed (nothing when no commit landed yet), then stop and report.
- Exit 1 from skill-diff, or an unmet prerequisite: stop and report.
- A service with no stop left after its live pin: compare its live pin with upstream's latest release. Screen each release since the pin by the plan's adoption rule (gbrain) or the service's notes and skip ranges, newest eligible first. One qualifies: report it as the proposed next stop (version, pin, date, screening evidence) for ssint-build-oci to add to the plan. None qualifies: report why for each candidate.
- Every run ends with one report: the stop taken or each hold met, the checks run with their results, any commit landed, and what the next run will do.

## Hermes skill review

Hermes loads only the skills in `manifests/hermes-skills.yaml`: `gbrain:` (names from garrytan/gbrain `skills/` at the commit the gbrain server runs) and `bundled:` (Hermes built-ins from the Hermes image, including always-on hermes-agent). It reaches gbrain over MCP with the bearer token hermes-mcp (scopes read,write,admin). Never print the token.

1. Run the stop's `skill_review.script`, with `<live pin>` from step 2 of the walk:
   `python3 .claude/skills/brain-upgrade/scripts/skill-diff.py gbrain <old-sha> <new-sha>`
   `python3 .claude/skills/brain-upgrade/scripts/skill-diff.py hermes <old-digest> <new-digest>`
   SHAs are full 40-hex; digests are `sha256:` index digests of nousresearch/hermes-agent. A Hermes image without an `org.opencontainers.image.revision` label (v0.21.6 on) resolves through its single `vX.Y.Z` Docker Hub tag to that tag's commit, named on stderr. `--allow-list FILE` tests a candidate list; by default it reads `manifests/hermes-skills.yaml` from simplesalt/brain's default branch. stdout is JSON (`added`, `removed`, `changed`, `allow_list`, `changed_any`); stderr has a summary.
2. Act on the exit code.
   - 0: nothing added, removed or changed. Note it and continue the step.
   - 3: something changed. Go on.
   - 1: could not tell. Stop and find out why; never guess.
   - 2: usage error. Fix the command.
3. Get the tools Hermes's token will have at the new version.
   - gbrain stop, rehearsal copy (ground truth): Deployment `gbrain-rehearsal` runs prod's startup script on a database copied from prod (Service `gbrain-rehearsal.ssint-main-ai:3131`). Through a simplesalt/brain commit by the brain workstream, set ConfigMap `gbrain-rehearsal-version` `GBRAIN_REHEARSAL_SHA` to the new SHA and bump its restart-epoch. Wait until `curl -s http://gbrain-rehearsal.ssint-main-ai:3131/health` is 200 with the new version. Call `tools/list` with the hermes-mcp bearer on the rehearsal copy and on prod (`gbrain.ssint-main-ai:3131`), and compare tool names, since sets differ at equal counts. Each call writes one request-log row under hermes-mcp. The rehearsal copy and prod accept the same bearer literal.
     ```
     TOK=$(yq -r 'select(.kind=="Deployment" and .metadata.name=="gbrain-rehearsal") | .spec.template.spec.containers[] | select(.name=="gbrain") | .env[] | select(.name=="GBRAIN_REMOTE_TOKEN") | .value' manifests/gbrain-rehearsal.yaml | head -1)
     curl -s -X POST http://<host>:3131/mcp -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | grep '^data:' | cut -c7- | jq -r '.result.tools[].name' | sort
     ```
     The rehearsal database is restored from a fresh prod dump for each hop, so it starts at prod's schema and rehearses exactly that hop; when no fresh restore is available, use the fallback.
   - gbrain stop, fallback (no cluster change, any SHA): read `docs/TOOL_CATALOG.md` at the new SHA (`gh api` raw), then remove:
     - tools hidden from a read,write,admin token (scopes agent, skill_editor, skill_publisher, skills_member_self are not implied by admin): submit_agent, get_agent_job, put_skill, delete_skill, get_skill_policy, set_skill_policy, join_brain, leave_brain, sync_brain_skills.
     - publish-gated tools whose switch is off: `mcp.publish_skills` (list_skills, get_skill, list_brain_skillpack, get_skill_asset; from v0.53 `gbrain init` turns it on only for a brand-new database, so it is off for ours unless the startup script sets it) and `mcp.publish_advisor` (advisor; our startup script turns it on).
     - Check `src/commands/serve-http.ts` and `src/mcp/publish-gates.ts` at the new SHA for new filters; this method breaks silently if gbrain adds one. Record that the list is static.
   - hermes-agent stop: gbrain's tools on prod do not change (list them with the call above against prod). The JSON's `new.revision` is the image's NousResearch/hermes-agent commit: read its Dockerfile and dependency files there for what each allow-listed built-in needs (binaries, Python libraries), and the release notes in the range for MCP client or toolset changes.
4. Read each added or changed `SKILL.md`, allow-listed ones first, old against new, against those tools: garrytan/gbrain `skills/` at both SHAs, or NousResearch/hermes-agent `skills/` at both `revision`s from the JSON (the image's bundled skills are that folder). Use `gh api` (raw.githubusercontent.com rate-limits).
5. Classify each as works, partly works or cannot work, with a one-line reason. An allow-listed skill that was removed or cannot work any more must be proposed for removal.
6. Put the additions and removals to the user in plain words with the reason for each, and wait. Nothing changes without their approval.
7. Write the approved list to `manifests/hermes-skills.yaml` in the same commit as the bump. The init log must then show the counts that file lists, with missing 0.

## Page history check

The only history of Hermes's gbrain pages is gbrain's `page_versions` table, and nothing in gbrain promises to keep it: it is unbounded only by accident (simplesalt/projects#926). Each gbrain stop confirms the new SHA still keeps it. `services.gbrain.page_history` in the plan holds the baseline with file and line references at the pin it was taken. Read the source at the new SHA with `gh api` raw (a tarball streamed through `python3 -I` with `tarfile` for repo-wide searches; `/tmp` is too small to extract it). Quote file:line for each finding.

1. Snapshot before every write. In `src/core/import-file.ts`, `tx.createVersion(slug, …)` still runs for an existing page before `tx.putPage` in the same transaction. Then on the rehearsal copy, with the hermes-mcp bearer, `put_page` a new page `ops/page-history-check-<new sha, 7 chars>`, then `edit_page` it once and `put_page` it once with `expected_revision`, calling `get_versions` after each write: each write must add one version. Stop if the call is gone or moved after the write, or a write adds no version.
2. No pruning of live pages' history. Search `src/` for statements that delete from or truncate `page_versions`, and for config keys or defaults that bound version age or count. Stop if anything removes versions of a page whose `deleted_at` is null, or bounds them to under 6 months or a fixed count. Do not judge by the oldest version's age: history only starts 2026-10-13.
3. Purge scope. Read `runPhasePurge` in `src/core/cycle.ts` and every function it calls (also `src/core/persistence/page-purge.ts`). Stop if the purge now hard-deletes pages or `page_versions` rows on any condition beyond the baseline, or its recovery window drops below the baseline's 72 hours.

## Changing the plan

The plan lives in simplesalt/oci under `builder-sb/policy/` (owner ssint-build-oci). An edit reaches pods only after a push to main publishes a new builder chart and the pod's builder chart version moves to it in simplesalt/brain (`manifests/coding-rc.yaml`, `manifests/coding-fe.yaml`). Other workstreams ask through an Effort assigned to ssint-build-oci.
