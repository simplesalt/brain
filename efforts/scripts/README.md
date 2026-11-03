# Effort scripts

The only copy of the Effort logic. The cluster Effort server (`manifests/efforts.yaml`, `scripts/effort-mcp.mjs`) mounts these files from the `effort-scripts` ConfigMap at `/opt/efforts/scripts` and runs them. Every agent reaches Efforts through that server.

They were moved here from `simplesalt/oci` `builder-sb/policy/scripts` at `ec056a1` (simplesalt/projects#868). Change them here, and add any new runtime file to the `effort-scripts` generator in `kustomization.yaml`.

Tests, which run offline against a fake `gh`:

```
python3 -I test-effort-schema.py
python3 -I test-effort-deferred-flows.py
```
