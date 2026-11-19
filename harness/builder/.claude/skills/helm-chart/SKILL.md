---
name: helm-chart
description: Use when writing, changing or reviewing a Helm chart, its templates, or its values.
---
<!-- agent-prompt -->
- One app per chart. Publish it to GHCR as an OCI package. Deploy it with a Flux HelmRelease from an OCI source at a pinned version.
- Put everything the app needs in its chart: workloads, volumes, Jobs, Secrets, its ServiceAccount, and a default-deny NetworkPolicy. Take shared operators and their CRDs from their own charts.
- Derive every resource name from the release name, and template every reference, so any number of releases run side by side.
- Make values the only config surface: a strict schema that rejects unknown keys, and defaults that render a working release.
- Pass simple settings as env or args. Ship deeper config as a digest-pinned OCI layer.
- Set resource limits, probes, a disruption budget, anti-affinity, the storage and HA annotations cluster policy expects, a non-root user and a read-only root filesystem.
- Use install and upgrade hooks for one-shot work. Trigger recurring work from events.
- Lint, render with defaults, and schema-check in CI before merge, with no cluster access.
- Define CRDs only in a repo that exists to publish that CRD.
