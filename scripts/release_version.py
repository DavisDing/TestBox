"""Semantic-version helpers used by the GitHub release workflow.

The release branch always moves forward from the latest formal ``vX.Y.Z`` tag.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

_VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_VERSION_FILES = (
    (Path("pyproject.toml"), re.compile(r'(?m)^version = "[^"]+"$'), 'version = "{version}"'),
    (Path("testbox/__init__.py"), re.compile(r'(?m)^__version__ = "[^"]+"$'), '__version__ = "{version}"'),
    (Path("installer/TestBox.iss"), re.compile(r'(?m)^#define AppVersion "[^"]+"$'), '#define AppVersion "{version}"'),
    (Path("installer/TestBoxUpdate.iss"), re.compile(r'(?m)^#define AppVersion "[^"]+"$'), '#define AppVersion "{version}"'),
    (Path("installer/TestBoxUpdate.iss"), re.compile(r'(?m)^#define UpdatePackage "[^"]+"$'), '#define UpdatePackage "TestBox-update-v{version}.zip"'),
)


def parse_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"Invalid semantic version: {value}")
    return tuple(int(part) for part in match.groups())


def next_release_version(declared_version: str, latest_tag: str | None) -> str:
    """Return the version to publish for a branch push.

    Without a prior formal tag, the declared version becomes the first release.
    Thereafter the automatic release increments the latest tag's patch level.
    Manual major/minor releases use an explicitly pushed matching ``vX.Y.Z``
    tag; branch pushes always use the next patch version.
    """
    declared = parse_version(declared_version)
    if not latest_tag:
        return declared_version
    latest_value = latest_tag[1:] if latest_tag.startswith("v") else latest_tag
    latest = parse_version(latest_value)
    return f"{latest[0]}.{latest[1]}.{latest[2] + 1}"


def sync_version_files(version: str, root: Path = Path(".")) -> list[Path]:
    """Synchronize all user-visible Windows release version declarations."""
    parse_version(version)
    root = root.resolve()
    changed: list[Path] = []
    for relative, pattern, replacement_template in _VERSION_FILES:
        path = root / relative
        text = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(replacement_template.format(version=version), text, count=1)
        if count != 1:
            raise ValueError(f"Could not update version in {path}")
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed.append(path)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve and synchronize TestBox release versions")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve", help="resolve the next release version")
    resolve.add_argument("--declared-version", required=True)
    resolve.add_argument("--latest-tag")

    sync = subparsers.add_parser("sync", help="synchronize release version files")
    sync.add_argument("--version", required=True)
    sync.add_argument("--root", type=Path, default=Path("."))

    args = parser.parse_args()
    try:
        if args.command == "resolve":
            print(next_release_version(args.declared_version, args.latest_tag))
        else:
            for path in sync_version_files(args.version, args.root):
                print(path)
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
