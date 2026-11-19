---
name: workload-design
description: Use when designing or changing a cluster workload - what it runs, where its data lives, how it reaches or is reached by other workloads, and what it may access.
---
<!-- agent-prompt -->
- Put each security boundary in its own namespace. A namespace and its ServiceAccount are the smallest unit of data access.
- Install each Helm release into a namespace named after the release. Kyverno creates the namespace.
- Grant access with labels in git. Reuse existing labels; add new ones the same way.
- Reach another workload through its Service: `service` in the same namespace, `service.namespace` across namespaces.
- Reach anything outside the cluster through its MCP.
- Keep each secret in one namespace, behind an auth proxy. Workloads that need what the secret unlocks call the proxy; the proxy is also their egress to that system.
- Keep a shared config value in one source. CI/CD copies it into each artifact that deploys it.
- Coordinate by push: events, webhooks, file watches. Poll only where a more trusted system reads from a less trusted one, as Flux reads git.
- Run one concern per pod. Give setup or authentication that is a security boundary its own namespace, and extend access to an existing provider.
- Write anything the app stores or remembers to a structured data store. File systems are injected volumes.
- Deploy Helm releases of OCI-published charts and images. Ship config that changes faster than the image as an OCI layer.
