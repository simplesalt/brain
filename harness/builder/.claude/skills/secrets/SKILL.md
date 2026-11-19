---
name: stupidproof-secrets
description: rules for coding agents to safely handle secrets. Use whenever you write or review application or infrastructure code, including manifests, automation, or any other code that touches credentials, connection strings, endpoints, account IDs, or keys, or when someone asks how to add, fill or rotate a secret.
license: PolyForm-Noncommercial-1.0.0 (see LICENSE)
metadata:
  version: "0.1.0"
---

<!-- agent-prompt -->

# Stupidproof secrets

Secret management best practices are wildly varied, inconsistent, and poorly understood. This set of rules describes a single, self-consistent, narrow approach for agents. 

## Approach

Each secret is necessary to establish trust between two systems or identities. However, each is a single point of failure for both breakage and compromise. As such, the best strategy is to minimize their use, storage, and dependence.

## 1. Decide what the value is

Ask: would this value change if the same app were deployed into a brand-new
environment?

- **Yes: it's environment-specific:** it's a secret. That includes values that
  protect nothing on their own (account and tenant IDs, hostnames, endpoints,
  OAuth client IDs, connection strings), because together they map how this
  environment connects to others, which is reconnaissance for an attacker.
- **It's published by an authoritative public source** (public DNS, a provider's
  OIDC discovery document, a vendor's documented endpoint): Not a secret. point the code at that
  source. Don't copy the value into the repo.
- **No, it belongs to the app and is the same everywhere:** Not a secret. it's app
  configuration and is committed with the app's GitOps code.

**Dummy secrets** belong in the last bin. Some codebases insist on secrets for security features that can be better delivered by dedicated platforms, such as encryption, user authentication, or database authentication. If working with such an app and your security design does not rely on the feature their secret enables, good security transparency requires that you transparently bypass it: if possible, disable the feature. If not, use a consistent, obviously fake value, publicly maintained with the rest of the app's configuration. That transparency clarifies that the secret and the features that rely on it are not load-bearing.  Using the secret is a "workaround" for the app's inflexible security configuration. 

- Don't alert on dummy secrets you find in code. Instead, verify whether that committed secret degrades the planned security model. 
- Never put dummy secrets into a secrets engine "to be safe". This will mislead you and others into thinking that it provides security value, and causing them to trust it as if it were a real secret. 

## 2. Store secrets only in secrets engines

A secrets engine is a platform purpose-built to manage, store, and provide secrets to authorized identities. Every cloud compute vendor provides one, and many cross-platform SaaS and self-hosted options exist. 

- Never persistently store a secret outside of a secrets engine, even if encrypted with SOPS or similar. Storage antipatterns include: 
  - database, config files, key material in .ssh or other app-specific directories.
  - If you find yourself packaging a secret to "look like a file", reconsider your approach: there is probably a better way to deliver the security properties you want. When an app truly insists on a file, keep the file off persistent storage, capture changes back into the secrets
  engine, and re-provision on restart.
- never store a secret locally for performance or platform resiliency reasons. always re-pull it from the authoritative single source. 
  - devices may maintain secrets to authenticate themselves. In this case, the secret and its storage is inherent to the identity of the device. 
- Configure the secret to only be accessible to the compute identity that uses it. 

## 3. A secret holds one value

- Exactly one value per secret. A client ID, a client secret and an account ID
  are three secrets, not one bundle.
- Only the literal value: no variable names, no `KEY=value` lines, no JSON
  wrapper, no file headers. For a private key, store the key material alone
  (its body, without header lines or line breaks); any surrounding format is
  assembled by the GitOps code.
- Context lives in GitOps code (Dockerfile, Helm chart, manifests): which env
  var or setting the value lands in, and how composites are assembled from
  single-value secrets at deploy time.

**Machine-managed secrets are the exception.** When automation provisions
credentials so that two systems it controls can talk to each other (for example,
a GitOps engine creating a cloud service account and storing its key for another
component), the automation must generate them randomly on deploy and stay operatorless: they must never be visible to a human or a human-accessible-system, such as logs.
Storage of machine-managed secrets may be structured and carry metadata, with schema part of the code contract between the systems. 

## 4. Code declares secrets; people fill them

- GitOps code and charts declare the secrets a workload needs, with no values.
- A person fills the value, outside Git, with a command that leaves no trace of
  it.
- gitops code should declare secret population as a dependency. 

## Kubernetes example

The workload's manifest or chart declares an empty Secret. With Flux, annotate
it so reconciliation doesn't wipe a value filled out of band:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: bearer-token
  annotations:
    kustomize.toolkit.fluxcd.io/ssa: merge
type: Opaque
```

To fill it, give the person this line with `NAME`, `NAMESPACE` and `KEY` populated. 

```bash
printf 'Value: '; IFS= read -rs v; echo; printf '{"data":{"KEY":"%s"}}' "$(printf '%s' "$v" | base64 | tr -d '\n')" | kubectl patch secret NAME -n NAMESPACE --type merge --patch-file /dev/stdin; unset v
```

Why:

- The value is read without echo and travels only through shell builtins and a
  pipe, so it never appears on the command line, in shell history, in the
  process list or in a temp file.
- `kubectl patch` fails if the Secret doesn't exist, so it can't silently create
  an undeclared one.
- It never uses `kubectl apply` (client-side apply copies the value into the
  `last-applied-configuration` annotation) or `--record` (writes the command
  into the `change-cause` annotation).

To confirm a Secret is filled, use: 
```
kubectl describe secret NAME -n NAMESPACE
```
