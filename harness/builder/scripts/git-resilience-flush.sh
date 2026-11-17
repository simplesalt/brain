#!/usr/bin/env python3
"""Stop hook — best-effort git checkpoint at the end of every turn.

Rationale (see simplesalt/projects#240): this pod holds no persistent state and
`permissions.defaultMode` is already `bypassPermissions`, so there is no human in the
loop to grant the "confirm before commit/push" signal the platform's generic default
implicitly waits for. Git is the durability layer here, not local disk — anything left
uncommitted at the end of a turn is exactly as exposed to pod teardown as an unsaved
edit. This hook is a backstop for whatever's left uncommitted, not a replacement for
committing thoughtfully as the agent works (see CLAUDE.md's "Git" section).

Guardrails, deliberately conservative:
  - Refuses to checkpoint when HEAD is behind the resolved default branch: a worktree that
    hasn't caught up to origin can hold stale copies of tracked files origin already
    advanced, and committing them would encode a revert of already-published work.
  - Only stages already-tracked-file changes (`git add -u`), never `git add -A`. A stray
    untracked file (secret, junk, half-finished scratch output) is never silently swept
    into an automated commit — that stays the agent's own deliberate `git add <path>`.
  - The commit is restricted to exactly the paths this invocation's own `git add -u`
    staged, never whatever else the index already held — so a later `git reset` can't
    carry unrelated staged content along invisibly. The commit message names those paths.
  - Push is always plain (never `--force`), so a diverged remote just rejects it rather
    than being overwritten.
  - Never pushes directly to the repo's resolved default branch (GitOps repos reconcile
    from a real branch; base-stack's is `named`, not even `main` — whatever `origin`
    actually resolves as default is treated as the deploy branch and left for a
    deliberate, reviewed push).
  - Never pushes a local worktree branch to `origin` either — a linked worktree's branch
    (`worktree-*`, one per concurrent session) is local-only isolation, not a feature
    branch; nothing ever cleans it up remotely, so pushing it just leaves an orphaned
    branch on `origin` forever (see CLAUDE.md's "Git" section). Detected via `git-dir` vs
    `git-common-dir` diverging, not by branch name, so it holds for any linked worktree.
  - Every other branch — i.e. one committed to from the repo's own (non-worktree)
    checkout, and not the default branch — is pushed automatically, since an unpublished
    branch there is just as exposed to pod teardown as an uncommitted diff and pushing it
    doesn't trigger any reconciliation.
  - Every step is wrapped so a failure anywhere (no git, detached HEAD, network error,
    a real pre-commit hook rejecting the commit, diverged remote, ...) is logged to
    stderr and never blocks the turn from ending: this script always exits 0.
"""
import json
import subprocess
import sys


def run(args, cwd):
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=30,
    )


def log(msg):
    print(f"git-resilience-flush: {msg}", file=sys.stderr)


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        data = {}
    cwd = data.get("cwd") or "."

    try:
        in_tree = run(["git", "rev-parse", "--is-inside-work-tree"], cwd)
        if in_tree.returncode != 0 or in_tree.stdout.strip() != "true":
            return
    except Exception as exc:
        log(f"not a git work tree or git unavailable, skipping ({exc})")
        return

    default_ref = ""
    try:
        default_ref = run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd).stdout.strip()
    except Exception as exc:
        log(f"could not resolve origin/HEAD, proceeding: {exc}")
    default_branch = default_ref.rsplit("/", 1)[-1] if default_ref else "main"

    try:
        head_rev = run(["git", "rev-parse", "HEAD"], cwd).stdout.strip()
        default_rev = run(["git", "rev-parse", f"refs/remotes/origin/{default_branch}"], cwd)
        if head_rev and default_rev.returncode == 0 and default_rev.stdout.strip() != head_rev:
            is_ancestor = run(
                ["git", "merge-base", "--is-ancestor", "HEAD", f"refs/remotes/origin/{default_branch}"],
                cwd,
            )
            if is_ancestor.returncode == 0:
                log(
                    f"HEAD is behind origin/{default_branch}; checkpointing here could encode "
                    f"a revert of work already published there — skipping"
                )
                return
    except Exception as exc:
        log(f"behind-check failed, proceeding without it: {exc}")

    try:
        pre_staged = {
            f for f in run(["git", "diff", "--cached", "--name-only"], cwd).stdout.splitlines() if f.strip()
        }
        run(["git", "add", "-u"], cwd)
        post_staged = {
            f for f in run(["git", "diff", "--cached", "--name-only"], cwd).stdout.splitlines() if f.strip()
        }
        files = sorted(post_staged - pre_staged)
        if not files:
            return

        message = f"chore: automated checkpoint ({len(files)} files)\n\n" + "\n".join(files)
        commit = run(["git", "commit", "-m", message, "--"] + files, cwd)
        if commit.returncode != 0:
            log(f"commit failed, leaving changes staged: {commit.stderr.strip()}")
            return
    except Exception as exc:
        log(f"commit step failed: {exc}")
        return

    try:
        branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd).stdout.strip()
        if not branch or branch == "HEAD":
            log("detached HEAD, skipping push")
            return

        if branch == default_branch:
            log(
                f"checkpoint committed on '{branch}', which is the resolved deploy "
                f"branch — skipping auto-push; push this deliberately when ready"
            )
            return

        git_dir = run(["git", "rev-parse", "--git-dir"], cwd).stdout.strip()
        common_dir = run(["git", "rev-parse", "--git-common-dir"], cwd).stdout.strip()
        if git_dir and common_dir and git_dir != common_dir:
            log(
                f"checkpoint committed on '{branch}', a local worktree branch — "
                f"worktree branches never reach origin; land the work by rebasing onto "
                f"origin/<target> and pushing straight to <target> instead"
            )
            return

        has_upstream = run(
            ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd
        ).returncode == 0
        push_args = ["git", "push"] if has_upstream else ["git", "push", "-u", "origin", branch]
        pushed = run(push_args, cwd)
        if pushed.returncode != 0:
            log(f"push failed, checkpoint commit stays local: {pushed.stderr.strip()}")
    except Exception as exc:
        log(f"push step failed: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"unexpected failure: {exc}")
    sys.exit(0)
