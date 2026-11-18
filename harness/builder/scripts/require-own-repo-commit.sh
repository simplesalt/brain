#!/usr/bin/env python3
"""PreToolUse hook — denies `git commit`/`git push` invocations whose target
directory belongs to a git repo other than the one this pod was provisioned
for, and warns upfront on a `git clone` of such a repo (simplesalt/oci
coding-hook request, no tracked Effort yet).

Why this exists: serve-rc.sh requires `$REPO_URL` and clones exactly that repo
into `$WORKDIR` (see builder-sb/serve-rc.sh) — that clone, plus whatever
worktrees are spawned off it, is this pod's own repo. Nothing stops an agent
from also `git clone`-ing a second, unrelated repo somewhere else in the pod
to go read its code, then, confused about which directory it is in, running
`git commit`/`git push` inside that second clone. A commit there pollutes a
repo this pod has no mandate over; a push does that to a remote other agents
and humans rely on. There is never a legitimate reason for a builder pod to
write git history into a repo that is not its own — the fix is always to hand
the work to an agent actually provisioned for that repo, via a new Effort, not
to let this pod commit there itself. The `git clone` warning exists so that
lesson lands before the agent has done any work in the wrong clone, not after
a denied commit throws that work away.

Detection (commit/push): resolve the git toplevel for the Bash tool call's
cwd, read that repo's `origin` remote, and compare it against `$REPO_URL`
(normalized: lower-cased host, no trailing `.git` or `/`, since serve-rc.sh
clones `$REPO_URL` verbatim as `origin` and the pod's git credential setup is
a plain `credential.helper store` for github.com over HTTPS — no `insteadOf`
URL rewriting — so the two should be textually identical modulo those
cosmetic differences). A mismatch denies; anything this hook cannot
confidently resolve (no git repo, no `origin`, `$REPO_URL` unset) fails open,
per this directory's house style of never fabricating a verdict it cannot
verify.

Detection (clone): best-effort extraction of the target URL from a matched
`git clone` invocation's trailing args (token splitting, skipping flags and
the handful of `git-clone` flags that take a separate value). If a URL is
found and normalizes to something other than `$REPO_URL`, warn (`allow` with
a reason, same non-blocking mechanism strip-comments-precommit.py uses to
report what it did) that changes made in that clone can't be committed from
here. If no URL can be confidently extracted, still warn, but generically —
this hook has genuine information (any clone here that isn't `$REPO_URL` is
subject to the commit/push deny above) even when it can't name the specific
target, and withholding the warning would just mean the agent finds out the
hard way at commit time instead.

Known limitations (be honest, don't overclaim):
  - The `git commit`/`git push` matching is a regex over the raw Bash command
    string, not a real shell parse. A `cd
    <other-repo> && git commit ...` moves the actual working directory before
    the commit runs, but this hook only ever inspects the Bash tool call's
    starting `cwd` (the field the harness passes), not any `cd` inside the
    command text. That specific evasion shape is a known gap, matching the
    same class of gap documented in require-no-sed-awk.sh for `sed`/`awk`.
  - `git -C <dir> commit`/`git -C <dir> push` similarly targets a directory
    this hook does not parse out and check; only the call's starting `cwd` is
    checked.
  - `$REPO_URL == origin` is an assumption this hook is trusting, not
    reproving from first principles each run: if some future pod provisioning
    path stops making that guarantee (e.g. an `insteadOf` rewrite, a mirror,
    or a differently-named remote), this hook has no independent way to
    detect that and would either false-deny or silently stop checking
    anything, depending on which assumption broke. If commits ever get denied
    on the pod's own, correct repo, re-verify this assumption first.
  - The clone-target parse is best-effort token splitting, not a real
    argument parser. A flag this hook doesn't know takes
    a value (and so doesn't skip) can shift the wrong token into the "URL"
    position, or a URL passed via shell expansion/variable isn't visible in
    the raw command text at all. Either way this only widens when the generic
    (rather than named) warning fires, never suppresses the warning outright.
  - The clone warning is advisory only — it never blocks the `git clone`
    itself, since reading another repo's code is legitimate; only a later
    commit/push into it is denied.
"""
import json
import os
import re
import subprocess
import sys

COMMIT_OR_PUSH_RE = re.compile(
    r'(?:^|(?<=[;&|(]))\s*git\s+(?:[\w.:/@=-]+\s+)*(?P<sub>commit|push)\b'
)

CLONE_RE = re.compile(
    r'(?:^|(?<=[;&|(]))\s*git\s+(?:[\w.:/@=-]+\s+)*clone\b(?P<rest>[^&;|]*)'
)

CLONE_FLAGS_WITH_VALUE = {
    "-o", "--origin", "-b", "--branch", "-u", "--upload-pack",
    "--reference", "--reference-if-able", "--separate-git-dir", "--depth",
    "--shallow-since", "--shallow-exclude", "-j", "--jobs", "--template",
    "-c", "--config", "--filter", "--bundle-uri",
}


def log(msg):
    print(f"require-own-repo-commit: {msg}", file=sys.stderr)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def allow(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def run(args, cwd, timeout=15):
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )


def normalize_git_url(url):
    url = url.strip()
    url = re.sub(r"^[\w+]+://(?:[^@/]+@)?", "", url)
    url = re.sub(r"^[^@/]+@", "", url)
    url = url.replace(":", "/", 1)
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[: -len(".git")]
    return url.lower()


def repo_shortname(url):
    return normalize_git_url(url).rsplit("/", 1)[-1]


def parse_clone_target(rest):
    """Best-effort first-positional-arg extraction from a matched `git clone`
    invocation's trailing args, skipping flags and the handful of git-clone
    flags known to take a separate value. Returns None if nothing found."""
    tokens = rest.split()
    positional = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            if token in CLONE_FLAGS_WITH_VALUE:
                skip_next = True
            continue
        positional.append(token)
    return positional[0] if positional else None


def check_commit_or_push(command, data):
    if not COMMIT_OR_PUSH_RE.search(command):
        return

    repo_url = os.environ.get("REPO_URL")
    if not repo_url:
        log("$REPO_URL not set, cannot verify which repo is this pod's own — allowing")
        return

    cwd = data.get("cwd") or os.getcwd()

    try:
        top = run(["git", "rev-parse", "--show-toplevel"], cwd)
        if top.returncode != 0 or not top.stdout.strip():
            return
        root = top.stdout.strip()

        origin = run(["git", "remote", "get-url", "origin"], root)
        if origin.returncode != 0 or not origin.stdout.strip():
            log(f"no 'origin' remote at {root}, cannot verify — allowing")
            return
        origin_url = origin.stdout.strip()
    except Exception as exc:
        log(f"detection failed, allowing (fail open): {exc}")
        return

    if normalize_git_url(origin_url) == normalize_git_url(repo_url):
        return

    own_name = repo_shortname(repo_url)
    other_name = repo_shortname(origin_url)
    deny(
        f"No — this directory ({root}) is a clone of {origin_url}, not this "
        f"pod's own repo ({repo_url}). This pod is only provisioned to commit "
        f"and push to {own_name}; writing git history into {other_name} would "
        "land it in a repo no one asked this agent to change, with no other "
        "session expecting or reviewing it there. Discard whatever you were "
        f"about to commit here. If {other_name} genuinely needs a change, "
        "tell your requester \"I can't do that because it needs other agents\" "
        f"and name ssint-build-{other_name} as the owner of that part — "
        "don't make that change from here."
    )


def check_clone(command):
    match = CLONE_RE.search(command)
    if not match:
        return

    repo_url = os.environ.get("REPO_URL")
    if not repo_url:
        return

    try:
        target = parse_clone_target(match.group("rest"))
    except Exception as exc:
        log(f"clone-target parse failed, warning generically: {exc}")
        target = None

    if target is None:
        allow(
            f"Heads up: this pod can only commit/push to its own repo "
            f"({repo_url}). Couldn't confidently tell what this `git clone` "
            "targets, but if it's a different repo, you can read that clone "
            "freely — a later `git commit`/`git push` inside it will be "
            "denied. If that repo genuinely needs a change, tell your "
            "requester \"I can't do that because it needs other agents\" and "
            "name ssint-build-<repo> as its owner instead of trying to commit "
            "it from here."
        )

    if normalize_git_url(target) == normalize_git_url(repo_url):
        return

    other_name = repo_shortname(target)
    allow(
        f"Heads up: {target} is not this pod's own repo ({repo_url}) — you "
        f"can read this clone of {other_name} freely, but a later `git "
        "commit`/`git push` inside it will be denied (this pod is only "
        f"provisioned for its own repo). If {other_name} genuinely needs a "
        "change, tell your requester \"I can't do that because it needs "
        f"other agents\" and name ssint-build-{other_name} as its owner "
        "instead of trying to commit it from here."
    )


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    if data.get("tool_name") != "Bash":
        return

    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not command:
        return

    check_commit_or_push(command, data)
    check_clone(command)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"unexpected failure, allowing commit/push unconfirmed (fail open): {exc}")
    sys.exit(0)
