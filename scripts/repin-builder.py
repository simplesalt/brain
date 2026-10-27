#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REGISTRY_HOST = "https://ghcr.io"
CHART_REPOSITORY = "simplesalt/charts/builder"
REQUEST_ATTEMPTS = 3

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_FILES = (
    REPO_ROOT / "manifests" / "coding-rc.yaml",
    REPO_ROOT / "manifests" / "coding-fe.yaml",
)

SELECT_BUILDER_RELEASES = 'select(.kind == "HelmRelease" and .spec.chart.spec.chart == "builder")'
PLAIN_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
NEXT_LINK = re.compile(r"<([^>]+)>\s*;\s*rel=\"?next\"?")


def open_with_retries(request):
    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        try:
            return urllib.request.urlopen(request, timeout=30)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt == REQUEST_ATTEMPTS:
                raise SystemExit(f"registry request failed: {request.full_url}: {error}")
            time.sleep(2 * attempt)


def published_tags():
    scope = urllib.parse.quote(f"repository:{CHART_REPOSITORY}:pull", safe=":/")
    with open_with_retries(urllib.request.Request(f"{REGISTRY_HOST}/token?scope={scope}")) as response:
        token = json.load(response)["token"]
    url = f"{REGISTRY_HOST}/v2/{CHART_REPOSITORY}/tags/list?n=1000"
    tags = []
    while url:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with open_with_retries(request) as response:
            tags.extend(json.load(response).get("tags") or [])
            next_link = NEXT_LINK.search(response.headers.get("Link", ""))
        url = urllib.parse.urljoin(url, next_link.group(1)) if next_link else None
    return tags


def newest_version(tags):
    plain = [tag for tag in tags if PLAIN_VERSION.match(tag)]
    if not plain:
        raise SystemExit(f"no X.Y.Z tags published for {CHART_REPOSITORY}")
    return max(plain, key=lambda tag: tuple(int(part) for part in tag.split(".")))


def builder_pins(path):
    listing = subprocess.run(
        ["yq", "-N", f'{SELECT_BUILDER_RELEASES} | .metadata.name + " " + .spec.chart.spec.version', str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return [tuple(line.split(" ", 1)) for line in listing.splitlines() if line.strip()]


def write_pins(path, version):
    subprocess.run(
        ["yq", "-i", f"({SELECT_BUILDER_RELEASES} | .spec.chart.spec.version) = strenv(BUILDER_VERSION)", str(path)],
        check=True, env={**os.environ, "BUILDER_VERSION": version},
    )


def main():
    parser = argparse.ArgumentParser(description="Repin every builder HelmRelease to the newest published builder chart.")
    parser.add_argument("--dry-run", action="store_true", help="print the moves without writing")
    args = parser.parse_args()

    target = newest_version(published_tags())
    pins = {path: builder_pins(path) for path in MANIFEST_FILES}
    release_count = sum(len(found) for found in pins.values())
    if not release_count:
        raise SystemExit("no builder HelmReleases found")

    moves = [(path, name, old) for path, found in pins.items() for name, old in found if old != target]
    if not moves:
        print(f"all {release_count} builder releases already current at {target}")
        return 0

    for _, name, old in moves:
        print(f"{name}: {old} -> {target}")
    if not args.dry_run:
        for path in {path for path, _, _ in moves}:
            write_pins(path, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
