# Effort scripts

The only copy of the Effort logic. The cluster Effort server (`manifests/efforts.yaml`, `scripts/effort-mcp.mjs`) mounts these files from the `effort-scripts` ConfigMap at `/opt/efforts/scripts` and runs them. Every agent reaches Efforts through that server.

They were moved here from `simplesalt/oci` `builder-sb/policy/scripts` at `ec056a1` (simplesalt/projects#868). Change them here, and add any new runtime file to the `effort-scripts` generator in `kustomization.yaml`.

Tests, which run offline against a fake `gh`:

```
python3 -I test-effort-schema.py
python3 -I test-effort-deferred-flows.py
```

The server has two roles: `/mcp/builder` (alias `/mcp/doer`) offers only `effort_find` and `effort_get`; `/mcp/owner` (alias `/mcp/coordinator`) offers every tool, including `effort_held` (`held-efforts.sh`, the Efforts a session holds). Each role's guide to reading an Effort is sent once, as the `initialize` instructions. `effort_select` and `effort_update` results carry `session_title`. `update-effort.sh --task-session <id> <address>` records the session a How task was dispatched to; `get-effort.sh` returns the parsed How items as `tasks`, each with its `session`.

Every issue in `simplesalt/projects` is an Effort. One with no row on the SimpleSalt Delivery board is added the first time it is read or selected (Status from its open or closed state, Assigned from its body), except on the read-only doer endpoint (`EFFORT_NO_ADOPT=1`), which reads it with `on_board: false`. `update-effort.sh` changes every field in place, including `--title`, `--type`, `--parent`/`--no-parent` and the Why/What/Where text; text above the first `###` heading is kept. `create-effort.sh` never guesses a parent: without `--parent` it creates a top-level Effort and returns the most related one as `parent_suggestion`.
