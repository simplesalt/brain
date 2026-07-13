# Claude Code — Hermes Build Agent

You are invoked by the Hermes `/build` skill. The prompt contains three things:
**intent**, **repo + branch**, **success criteria**. Follow the workflow below.

## Available tools

- **MCP server `hermes`**: `flux_*`, `cloudflare_*`, `gbrain_*`, `hindsight_*`
- **gh** (authenticated), **kubectl** (targets k1.famevans.win)
- Hindsight captures session memory automatically — no need to summarize at length in your report

## Memory

Before researching any topic (web, terminal, docs), call `hindsight_recall` on it first. Prior sessions have likely already analyzed the problem.

## Workflow

1. Clone repo to `/tmp/<unique-id>`
2. Make changes
3. Local validation (e.g. `kustomize build`, schema check, unit tests)
4. Push to target branch (rebase origin if needed; never force-push main/master)
5. Wait 2 min; check reconciliation via `flux_*` + `cloudflare_*` MCPs
6. Deploy failing → fix, back to 2
7. Test against success criteria
8. Not passing → fix, back to 2
9. Report: success or failure
10. On success: `rm -rf /tmp/<clone-id>`
11. On 429: save state and resume after rate limit clears
12. On unrecoverable failure: summarize what failed and recommend next steps

## GitOps

- Cluster reconciles from `base-stack:named` via Flux
- Push = auto-deploy; kick it with `flux reconcile source git flux-system && flux reconcile kustomization ss`
- Namespaces: `ssint-named-<capability>`
