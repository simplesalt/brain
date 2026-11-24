---
name: change
description: Use whenever you change anything in a repo - code, manifests, Helm charts, images, Crossplane resources, CI or config - whether it is a fix or a new feature.
---
<!-- agent-prompt -->
- Every system you work on is declared by a GitOps repo. Change the system by changing that repo.
- First find where the repo already declares the thing you are changing. Change it there, and remove what your change replaces.
- Follow the existing standard. When it seems to block you, your approach is what needs to change; if you cannot find a way within the standard, ask the person.
- When the same failure comes back after a fix, stop changing things. Turn on debug output, read the whole log, and find the root cause before the next change.
- Fix the cause of a failure in our own code or config. Use a scheduled cleanup only for one-off failures with no known cause, or for causes inside a system we don't control. Cost is never the reason to choose cleanup.
- Treat a change to an internet-public endpoint as a production change.
- Unless the person asks for it, never choose a name or label in code (resources, variables, objects, tags, labels, database fields) with a legal, regulatory or law-enforcement meaning, such as audit, compliant, consent, decree, lawsuit, legal, HIPAA, DoD, FDA or ENISA.
- Before you build something, find the highest-level building block the platform already uses for that need, and use it. Build from lower-level pieces only when no existing block fits.
