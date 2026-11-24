---
name: workload-design
description: Use when designing or changing a cluster workload - what it runs, where its data lives, how it reaches or is reached by other workloads, and what it may access.
---
<!-- agent-prompt -->
- Treat everything in a namespace as one access domain: any pod in it can reach any secret or service in it. To keep something apart, put it in another namespace.
- Install each Helm release into its own namespace, named after the release. Do not declare the Namespace.
- A workload must reach a Service in another namespace only through a shared access label: the workload and the Service carry the same label. Example: a Deployment in `chicken` needs Service `poultry-feed` in `food`, so both carry `chickens-eat-poultry-feed`. Label a Service that only its own namespace may use `internal`.
- Reach another workload through its Service: `service` in the same namespace, `service.namespace` across namespaces.
- Any workload that needs a system outside the cluster must reach it through that system's auth proxy, labelled for access to it. A workload must never hold the credential the proxy uses, or any copy of it. Only auth proxies, including MCPs, may have egress from the cluster, and each proxy may reach only the system it proxies.
- Generate every use of a shared value from one source, through CI/CD or a Kyverno template. If you are copying and pasting a value or code, find its source instead.
- Coordinate workloads by push (events, webhooks, file watches) wherever the design allows. Use pull only when the system you are adopting leaves no push option.
- If you are thinking of adding a sidecar, first use Kubernetes built-in health checks and a correctly built image; they usually meet the need more reliably. A sidecar fits when a packaged app you don't want to modify must behave differently, for example in how it stores data or handles secrets. Example: a sidecar that watches for Claude Code's auth token file and moves the values into a Kubernetes Secret. A sidecar shares its pod's access, so it is never a security boundary.
- Store app data by kind:
  - Config: mount it read-only from a git-backed source (an OCI layer, or git).
  - Input: read it from the system it comes from, when you need it.
  - Work product: write it to the system where it belongs.
  - An app that insists on a local database file (SQLite): use a resilient, replicated volume.
  - Last resort: a filesystem backed by version-controlled storage.
  Block- or filesystem-level replication is never resilient enough.
- Deploy each app as a Flux HelmRelease of an OCI-published chart and image.
