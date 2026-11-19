---
name: kyverno-policy
description: Use when writing, changing or reviewing a Kyverno policy of any kind, including cleanup policies.
---
<!-- agent-prompt -->
- Act only at admission: validate, set defaults, or generate companion objects for the resource being admitted.
- Tie each generated object to its trigger so it is removed with it.
- Generate access grants and NetworkPolicies from labels when useful. The labels in git remain the access decision.
- When a policy would need to act after admission, change what the repo declares instead. Flux owns state after admission.
