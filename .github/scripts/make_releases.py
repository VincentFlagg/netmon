"""Create GitHub Releases from CHANGELOG.md entries.

Run by .github/workflows/release.yml. Uses the `gh` CLI (authenticated via the
workflow's GITHUB_TOKEN) to create a release per version, idempotently — a
version that already has a release is skipped.
"""

import os
import re
import subprocess
import sys


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True)


def current_version() -> str | None:
    for line in open("pyproject.toml"):
        m = re.match(r'\s*version\s*=\s*"([^"]+)"', line)
        if m:
            return m.group(1)
    return None


def parse_changelog() -> tuple[dict, list]:
    """Return ({version: notes}, [versions newest-first])."""
    text = open("CHANGELOG.md").read()
    versions: dict = {}
    order: list = []
    for part in re.split(r"(?m)^## \[", text)[1:]:
        m = re.match(r"(\d+\.\d+\.\d+)\]\s*-\s*(\d{4}-\d{2}-\d{2})", part)
        if not m:
            continue  # e.g. the [Unreleased] section
        ver = m.group(1)
        body = part[m.end():]
        # Drop the link-reference block at the end of the file.
        body = re.split(r"(?m)^\[[^\]]+\]:\s*https?://", body)[0].strip()
        versions[ver] = body
        order.append(ver)
    return versions, order


def main() -> int:
    versions, order = parse_changelog()
    event = os.environ.get("EVENT_NAME", "")
    inp = (os.environ.get("INPUT_VERSION") or "").strip()
    sha = os.environ.get("TARGET_SHA", "")

    if event == "workflow_dispatch":
        if inp in ("", "current"):
            targets = [current_version()]
        elif inp.lower() == "all":
            targets = order[::-1]  # oldest first, so the newest ends up "latest"
        else:
            targets = [inp.lstrip("v")]
    else:  # push to main
        targets = [current_version()]

    created = []
    for ver in targets:
        if not ver:
            continue
        if ver not in versions:
            print(f"skip {ver}: no CHANGELOG entry")
            continue
        tag = f"v{ver}"
        if sh("gh", "release", "view", tag).returncode == 0:
            print(f"skip {tag}: release already exists")
            continue
        r = sh("gh", "release", "create", tag,
               "--title", tag, "--notes", versions[ver], "--target", sha)
        if r.returncode != 0:
            print(f"FAILED to create {tag}: {r.stderr}", file=sys.stderr)
            return 1
        print(f"created {tag}")
        created.append(tag)

    print("done; created:", created or "(nothing new)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
