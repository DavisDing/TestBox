"""Small synthetic delta ZIPs for installer smoke and source updater regression.

Unchanged bundle files are represented only by their installed manifest metadata;
this module never reads or copies a PyInstaller bundle. These fixtures exercise
real update validation, but do not themselves verify Windows native installers.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path

from testbox import updater


def _write_zip_atomic(output: Path, entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> None:
    """Commit a completely written ZIP; failures leave any existing output intact."""
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for entry, payload in entries:
                archive.writestr(entry, payload)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def build_delta_fixture(
    installed_manifest: dict,
    output: Path,
    *,
    version: str,
    replacements: dict[str, bytes],
    deleted: list[str],
) -> dict:
    """Build a synthetic delta against an installed complete manifest.

    Replacements may add new managed files or replace existing exact paths.
    Deletions must name old managed paths exactly (including case). Empty deltas
    are rejected; deletion-only and zero-byte replacement fixtures are supported.
    Caller-owned data, including unchanged file metadata, is never modified.
    """
    manifest = copy.deepcopy(installed_manifest)
    updater._validate_manifest(manifest)
    if not isinstance(version, str) or not version.strip() or version == manifest["version"]:
        raise ValueError("fixture version must be non-empty and different from the installed version")
    if not isinstance(replacements, dict) or not isinstance(deleted, list):
        raise ValueError("replacements must be a dict and deleted must be a list")
    if not replacements and not deleted:
        raise ValueError("fixture must contain at least one replacement or deletion")

    old_files = {item["path"]: item for item in manifest["files"]}
    for relative, payload in replacements.items():
        updater._managed_relative_path(relative)
        if not isinstance(payload, bytes):
            raise ValueError(f"fixture replacement must be bytes: {relative}")
    for relative in deleted:
        updater._managed_relative_path(relative)
        if relative not in old_files:
            raise ValueError(f"fixture cannot delete an unmanaged or non-exact old path: {relative}")
    if len(deleted) != len({relative.casefold() for relative in deleted}):
        raise ValueError("fixture contains duplicate deletions")
    if set(replacements) & set(deleted):
        raise ValueError("fixture cannot replace and delete the same path")

    files = {relative: item for relative, item in old_files.items()
             if relative not in replacements and relative not in deleted}
    for relative, payload in replacements.items():
        files[relative] = {"path": relative, "size": len(payload),
                           "sha256": hashlib.sha256(payload).hexdigest()}
    manifest.update(
        version=version,
        base_version=installed_manifest["version"],
        package=output.name,
        package_url=None,
        files=[files[relative] for relative in sorted(files)],
        changed_files=sorted(replacements),
        deleted_files=sorted(deleted),
    )
    # Reuse the production validator for protected/unsafe paths, case collisions,
    # file/directory conflicts, hashes and component identity. Keep the original
    # unchanged metadata rather than dropping any caller's supplemental fields.
    updater._validate_manifest(manifest)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _write_zip_atomic(output, [(updater.MANIFEST_NAME, manifest_bytes)] +
                      [(relative, replacements[relative]) for relative in manifest["changed_files"]])
    return manifest


def corrupt_payload(package: Path, destination: Path) -> None:
    """Verify a delta fixture, then flip one payload byte without changing its hash.

    ZIP CRCs are recomputed: the result is a readable ZIP that fails the updater's
    SHA-256 check, not a broken compressed stream. The source stays untouched.
    Empty/deletion-only fixtures cannot supply a byte to flip and are rejected.
    """
    if package.resolve() == destination.resolve():
        raise ValueError("corrupt fixture destination must differ from the original package")
    with zipfile.ZipFile(package) as archive:
        members: dict[str, zipfile.ZipInfo] = {}
        seen: set[str] = set()
        for info in archive.infolist():
            relative = updater._safe_relative_path(info.filename)
            if relative.casefold() in seen:
                raise ValueError(f"fixture contains duplicate ZIP paths: {relative}")
            seen.add(relative.casefold())
            kind = stat.S_IFMT(info.external_attr >> 16)
            if info.is_dir() or kind not in (0, stat.S_IFREG):
                raise ValueError(f"fixture contains a directory, link or special entry: {relative}")
            members[relative] = info
        if updater.MANIFEST_NAME not in members:
            raise ValueError("fixture is missing its update manifest")
        if members[updater.MANIFEST_NAME].file_size > 8 * 1024 * 1024:
            raise ValueError("fixture manifest is too large")
        manifest_bytes = archive.read(members[updater.MANIFEST_NAME])
        manifest = updater._validate_manifest(json.loads(manifest_bytes.decode("utf-8")))
        if not manifest.get("base_version") or manifest["version"] == manifest["base_version"]:
            raise ValueError("fixture must describe a delta to a different version")
        changed = manifest["changed_files"]
        if set(members) != {updater.MANIFEST_NAME, *changed}:
            raise ValueError("fixture contains missing or unlisted ZIP payloads")
        files = {item["path"]: item for item in manifest["files"]}
        entries: list[tuple[str | zipfile.ZipInfo, bytes]] = []
        chosen = None
        for relative in sorted(changed):
            expected = files[relative]
            if members[relative].file_size != expected["size"]:
                raise ValueError(f"fixture payload size mismatch: {relative}")
            payload = archive.read(members[relative])
            if hashlib.sha256(payload).hexdigest() != expected["sha256"]:
                raise ValueError(f"fixture payload hash mismatch: {relative}")
            if chosen is None and payload:
                chosen = relative
                payload = bytes([payload[0] ^ 1]) + payload[1:]
            entries.append((members[relative], payload))
        if chosen is None:
            raise ValueError("fixture has no non-empty changed payload to corrupt")
        entries.insert(0, (members[updater.MANIFEST_NAME], manifest_bytes))
    _write_zip_atomic(destination, entries)
