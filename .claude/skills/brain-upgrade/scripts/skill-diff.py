#!/usr/bin/env python3
"""Report which skills changed between two gbrain commits or two Hermes image digests.

usage: skill-diff.py gbrain <old-sha> <new-sha> [--allow-list FILE]
       skill-diff.py hermes <old-digest> <new-digest> [--allow-list FILE]

Prints one JSON object on stdout and a short summary on stderr.
Exit codes: 0 nothing added, removed or changed; 3 something changed;
1 could not determine; 2 usage error.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

EXIT_UNCHANGED = 0
EXIT_UNDETERMINED = 1
EXIT_CHANGED = 3

SKILL_FILE = "SKILL.md"
GBRAIN_REPO = "garrytan/gbrain"
HERMES_SOURCE_REPO = "NousResearch/hermes-agent"
HERMES_IMAGE = "nousresearch/hermes-agent"
BRAIN_REPO = "simplesalt/brain"
ALLOW_LIST_PATH = "manifests/hermes-skills.yaml"
REVISION_LABEL = "org.opencontainers.image.revision"

TOKEN_URL = (
    "https://auth.docker.io/token?service=registry.docker.io"
    f"&scope=repository:{HERMES_IMAGE}:pull"
)
REGISTRY_URL = f"https://registry-1.docker.io/v2/{HERMES_IMAGE}"
HUB_HOST = "hub.docker.com"
HUB_TAGS_URL = f"https://{HUB_HOST}/v2/namespaces/nousresearch/repositories/hermes-agent/tags?page_size=100"
HUB_TAG_PAGE_LIMIT = 100
TAG_PEEL_LIMIT = 5
MANIFEST_ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
])

GH_TIMEOUT_SECONDS = 90
HTTP_TIMEOUT_SECONDS = 30
YQ_TIMEOUT_SECONDS = 30
BLOB_READ_WORKERS = 8

GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
NAME_LINE_RE = re.compile(r"^name[ \t]*:[ \t]*(.*)$")
QUOTED_VALUE_RE = re.compile(r"""^(["'])(.*?)\1[ \t]*(?:#.*)?$""")
TRAILING_COMMENT_RE = re.compile(r"(^|[ \t])#.*$")
LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")


class SkillDiffError(Exception):
    pass


@dataclass(frozen=True)
class SkillFolder:
    path: str
    tree_sha: str
    file_sha: str


@dataclass(frozen=True)
class Skill:
    name: str
    path: str
    fingerprint: str


@dataclass(frozen=True)
class Snapshot:
    ref: str
    revision: str
    skills: dict


@dataclass(frozen=True)
class Kind:
    section: str
    repo: str
    max_depth: int | None


KINDS = {
    "gbrain": Kind(section="gbrain", repo=GBRAIN_REPO, max_depth=1),
    "hermes": Kind(section="bundled", repo=HERMES_SOURCE_REPO, max_depth=None),
}


def unquote_scalar(value):
    quoted = QUOTED_VALUE_RE.match(value)
    if quoted:
        return quoted.group(2).strip()
    plain = TRAILING_COMMENT_RE.sub("", value).strip()
    if plain[:1] in ("|", ">"):
        return ""
    return plain


def parse_frontmatter_name(text):
    lines = LINE_BREAK_RE.split(text.lstrip("﻿"))
    if lines[0].rstrip() != "---":
        return None
    for line in lines[1:]:
        if line.rstrip() in ("---", "..."):
            return None
        match = NAME_LINE_RE.match(line)
        if match:
            return unquote_scalar(match.group(1)) or None
    return None


def find_skill_folders(entries, max_depth=None):
    tree_shas = {e["path"]: e["sha"] for e in entries if e.get("type") == "tree"}
    folders = []
    for entry in entries:
        if entry.get("type") != "blob":
            continue
        folder, _, filename = entry["path"].rpartition("/")
        if filename != SKILL_FILE or not folder:
            continue
        if max_depth is not None and folder.count("/") + 1 > max_depth:
            continue
        if folder not in tree_shas:
            raise SkillDiffError(f"tree listing has no entry for folder {folder}")
        folders.append(SkillFolder(folder, tree_shas[folder], entry["sha"]))
    return sorted(folders, key=lambda folder: folder.path)


def index_skills(folders, names_by_file_sha):
    skills = {}
    for folder in folders:
        name = names_by_file_sha[folder.file_sha]
        path = f"skills/{folder.path}"
        if name in skills:
            raise SkillDiffError(
                f"skill name {name!r} is used by both {skills[name].path} and {path}"
            )
        skills[name] = Skill(name, path, folder.tree_sha)
    return skills


def compare_skills(old, new):
    added = [{"name": n, "path": new[n].path} for n in sorted(new.keys() - old.keys())]
    removed = [{"name": n, "path": old[n].path} for n in sorted(old.keys() - new.keys())]
    changed = []
    unchanged = 0
    for name in sorted(old.keys() & new.keys()):
        before, after = old[name], new[name]
        if before.fingerprint == after.fingerprint and before.path == after.path:
            unchanged += 1
        else:
            changed.append({"name": name, "old_path": before.path, "new_path": after.path})
    return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged}


def flag_allow_list(section, names, comparison, new_names):
    wanted = sorted(set(names))
    changed = {entry["name"] for entry in comparison["changed"]}
    removed = {entry["name"] for entry in comparison["removed"]}
    return {
        "section": section,
        "names": wanted,
        "changed": [name for name in wanted if name in changed],
        "removed": [name for name in wanted if name in removed],
        "missing_in_new": [name for name in wanted if name not in new_names],
    }


def describe_snapshot(snapshot):
    return {
        "ref": snapshot.ref,
        "revision": snapshot.revision,
        "count": len(snapshot.skills),
    }


def build_report(kind, old, new, allow_names):
    comparison = compare_skills(old.skills, new.skills)
    changed_any = bool(comparison["added"] or comparison["removed"] or comparison["changed"])
    return {
        "kind": kind,
        "old": describe_snapshot(old),
        "new": describe_snapshot(new),
        **comparison,
        "allow_list": flag_allow_list(
            KINDS[kind].section, allow_names, comparison, set(new.skills)
        ),
        "changed_any": changed_any,
    }


def exit_code(report):
    return EXIT_CHANGED if report["changed_any"] else EXIT_UNCHANGED


def extract_allow_names(document, section):
    names = document.get(section) if isinstance(document, dict) else None
    if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
        raise SkillDiffError(f"allow-list has no list of names under {section!r}")
    return names


def parse_json(raw, what):
    try:
        return json.loads(raw)
    except ValueError as error:
        raise SkillDiffError(f"{what} is not valid JSON: {error}")


def gh_api(path, raw=False):
    command = ["gh", "api"]
    if raw:
        command += ["-H", "Accept: application/vnd.github.raw"]
    command.append(path)
    try:
        result = subprocess.run(command, capture_output=True, timeout=GH_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SkillDiffError(f"gh api {path} failed: {error}")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise SkillDiffError(f"gh api {path} failed: {detail}")
    return result.stdout


def yaml_to_object(source):
    try:
        result = subprocess.run(
            ["yq", "-o=json", "."], input=source, capture_output=True,
            timeout=YQ_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SkillDiffError(f"yq failed: {error}")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise SkillDiffError(f"allow-list is not valid YAML: {detail}")
    return parse_json(result.stdout, "yq output")


def read_allow_list_source(path):
    if path is None:
        return gh_api(f"repos/{BRAIN_REPO}/contents/{ALLOW_LIST_PATH}", raw=True)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as error:
        raise SkillDiffError(f"cannot read allow-list {path}: {error}")


def load_allow_names(path, section):
    return extract_allow_names(yaml_to_object(read_allow_list_source(path)), section)


def fetch_tree_entries(repo, revision):
    document = parse_json(
        gh_api(f"repos/{repo}/git/trees/{revision}:skills?recursive=1"),
        f"{repo}@{revision} skills tree",
    )
    if document.get("truncated"):
        raise SkillDiffError(f"{repo}@{revision} skills tree listing is truncated")
    return document["tree"]


def fetch_skill_name(repo, folder):
    text = gh_api(f"repos/{repo}/git/blobs/{folder.file_sha}", raw=True)
    name = parse_frontmatter_name(text.decode("utf-8", "replace"))
    if not name:
        raise SkillDiffError(f"{repo}: skills/{folder.path}/{SKILL_FILE} has no frontmatter name")
    return name


def read_skill_names(repo, folders):
    by_file_sha = {folder.file_sha: folder for folder in folders}
    with ThreadPoolExecutor(max_workers=BLOB_READ_WORKERS) as pool:
        names = pool.map(lambda folder: fetch_skill_name(repo, folder), by_file_sha.values())
        return dict(zip(by_file_sha, names))


def load_snapshots(repo, max_depth, ref_revisions):
    folders_by_revision = {
        revision: find_skill_folders(fetch_tree_entries(repo, revision), max_depth)
        for _, revision in ref_revisions
    }
    all_folders = [f for folders in folders_by_revision.values() for f in folders]
    names = read_skill_names(repo, all_folders)
    return [
        Snapshot(ref, revision, index_skills(folders_by_revision[revision], names))
        for ref, revision in ref_revisions
    ]


class DropAuthOnForeignRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        same_host = urllib.parse.urlsplit(newurl).netloc == urllib.parse.urlsplit(req.full_url).netloc
        if redirected is not None and not same_host:
            redirected.remove_header("Authorization")
        return redirected


HTTP_OPENER = urllib.request.build_opener(DropAuthOnForeignRedirect)


def http_get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with HTTP_OPENER.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        hint = " (Docker Hub rate limit reached, retry later)" if error.code == 429 else ""
        raise SkillDiffError(f"GET {url} returned HTTP {error.code}{hint}")
    except (urllib.error.URLError, OSError) as error:
        raise SkillDiffError(f"GET {url} failed: {error}")


def registry_json(url, token, accept=None):
    headers = {"Authorization": f"Bearer {token}"}
    if accept:
        headers["Accept"] = accept
    document = parse_json(http_get(url, headers), url)
    if not isinstance(document, dict):
        raise SkillDiffError(f"{url} did not return a JSON object")
    return document


def docker_token():
    token = parse_json(http_get(TOKEN_URL), "Docker Hub token response").get("token")
    if not token:
        raise SkillDiffError("Docker Hub token response had no token")
    return token


def select_linux_amd64(index, digest):
    for entry in index["manifests"]:
        platform = entry.get("platform") or {}
        if platform.get("os") == "linux" and platform.get("architecture") == "amd64":
            return entry["digest"]
    raise SkillDiffError(f"{digest} has no linux/amd64 manifest")


def hermes_source_revision(digest, token):
    document = registry_json(f"{REGISTRY_URL}/manifests/{digest}", token, MANIFEST_ACCEPT)
    if "manifests" in document:
        platform_digest = select_linux_amd64(document, digest)
        document = registry_json(f"{REGISTRY_URL}/manifests/{platform_digest}", token, MANIFEST_ACCEPT)
    config_digest = (document.get("config") or {}).get("digest")
    if not config_digest:
        raise SkillDiffError(f"{digest} has no image config digest")
    config = registry_json(f"{REGISTRY_URL}/blobs/{config_digest}", token)
    labels = (config.get("config") or {}).get("Labels") or {}
    revision = labels.get(REVISION_LABEL)
    if isinstance(revision, str) and GIT_SHA_RE.match(revision):
        return revision
    return revision_from_release_tag(digest)


def release_tags_for_digest(digest):
    url, tags = HUB_TAGS_URL, set()
    for _ in range(HUB_TAG_PAGE_LIMIT):
        if urllib.parse.urlsplit(url).netloc != HUB_HOST:
            raise SkillDiffError(f"Docker Hub tag listing points outside {HUB_HOST}: {url}")
        page = parse_json(http_get(url), "Docker Hub tag listing")
        if not isinstance(page, dict):
            raise SkillDiffError("Docker Hub tag listing is not a JSON object")
        for entry in page.get("results") or []:
            name = entry.get("name") or ""
            if entry.get("digest") == digest and RELEASE_TAG_RE.match(name):
                tags.add(name)
        url = page.get("next")
        if not url:
            return sorted(tags)
    raise SkillDiffError(f"Docker Hub tag listing for {HERMES_IMAGE} exceeds {HUB_TAG_PAGE_LIMIT} pages")


def tag_commit(repo, tag):
    target = parse_json(gh_api(f"repos/{repo}/git/ref/tags/{tag}"), f"{repo} tag {tag}").get("object") or {}
    for _ in range(TAG_PEEL_LIMIT):
        kind, sha = target.get("type"), target.get("sha") or ""
        if kind == "commit" and GIT_SHA_RE.match(sha):
            return sha
        if kind != "tag":
            raise SkillDiffError(f"{repo} tag {tag} does not point at a commit")
        target = parse_json(gh_api(f"repos/{repo}/git/tags/{sha}"), f"{repo} tag object {sha}").get("object") or {}
    raise SkillDiffError(f"{repo} tag {tag} nests more than {TAG_PEEL_LIMIT} tag objects")


def revision_from_release_tag(digest):
    tags = release_tags_for_digest(digest)
    if len(tags) != 1:
        found = ", ".join(tags) if tags else "none"
        raise SkillDiffError(
            f"{digest} has no usable {REVISION_LABEL} label and needs exactly one vX.Y.Z tag on Docker Hub (found: {found})"
        )
    revision = tag_commit(HERMES_SOURCE_REPO, tags[0])
    print(f"skill-diff: {digest} has no {REVISION_LABEL} label; using release tag {tags[0]} at {revision}", file=sys.stderr)
    return revision


def resolve_revisions(kind, refs):
    if kind == "gbrain":
        return {ref: ref for ref in refs}
    token = docker_token()
    return {ref: hermes_source_revision(ref, token) for ref in dict.fromkeys(refs)}


def load_snapshot_pair(kind, old_ref, new_ref):
    settings = KINDS[kind]
    revisions = resolve_revisions(kind, [old_ref, new_ref])
    old, new = load_snapshots(
        settings.repo, settings.max_depth,
        [(old_ref, revisions[old_ref]), (new_ref, revisions[new_ref])],
    )
    return old, new


def run(args):
    allow_names = load_allow_names(args.allow_list, KINDS[args.kind].section)
    old, new = load_snapshot_pair(args.kind, args.old, args.new)
    return build_report(args.kind, old, new, allow_names)


def names_or_none(names):
    return ", ".join(names) if names else "none"


def format_summary(report):
    old, new, allow = report["old"], report["new"], report["allow_list"]
    return "\n".join([
        f"{report['kind']}: {old['count']} skills at {old['revision'][:8]}"
        f" -> {new['count']} skills at {new['revision'][:8]}",
        f"added {len(report['added'])}, removed {len(report['removed'])},"
        f" changed {len(report['changed'])}, unchanged {report['unchanged']}",
        f"allow-list ({allow['section']}, {len(allow['names'])} names):"
        f" changed: {names_or_none(allow['changed'])};"
        f" removed: {names_or_none(allow['removed'])};"
        f" missing in new: {names_or_none(allow['missing_in_new'])}",
        "review needed" if report["changed_any"] else "no skill added, removed or changed",
    ])


def git_sha(value):
    if not GIT_SHA_RE.match(value.lower()):
        raise argparse.ArgumentTypeError(f"{value!r} is not a full 40-hex commit sha")
    return value.lower()


def image_digest(value):
    if not IMAGE_DIGEST_RE.match(value.lower()):
        raise argparse.ArgumentTypeError(f"{value!r} is not a sha256:<64hex> digest")
    return value.lower()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="skill-diff.py",
        description="Report which skills changed between two gbrain commits or Hermes image digests.",
    )
    commands = parser.add_subparsers(dest="kind", required=True)
    for kind, ref_type, ref_label in (("gbrain", git_sha, "sha"), ("hermes", image_digest, "digest")):
        command = commands.add_parser(kind)
        command.add_argument("old", type=ref_type, metavar=f"old-{ref_label}")
        command.add_argument("new", type=ref_type, metavar=f"new-{ref_label}")
        command.add_argument("--allow-list", metavar="FILE", default=None)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        report = run(args)
    except SkillDiffError as error:
        print(f"skill-diff: {error}", file=sys.stderr)
        return EXIT_UNDETERMINED
    except Exception as error:
        print(f"skill-diff: unexpected {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_UNDETERMINED
    print(json.dumps(report, indent=2))
    print(format_summary(report), file=sys.stderr)
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
