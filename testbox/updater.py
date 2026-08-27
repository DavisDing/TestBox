"""Versioned manifest and incremental update support for Windows packages.

The updater deliberately manages only files listed in the release manifest. User
workspaces, configuration, and user-installed plugins live outside the install
root and are never touched by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_NAME = "update-manifest.json"
UPDATER_NAME = "TestBox-Updater.exe"
MANIFEST_SCHEMA_VERSION = 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(path: str) -> str:
    normalized = PurePosixPath(path)
    if normalized.is_absolute() or ".." in normalized.parts or not path or "\\" in path:
        raise ValueError(f"非法更新文件路径: {path}")
    return normalized.as_posix()


def _managed_files(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in {MANIFEST_NAME, UPDATER_NAME}:
            continue
        entries.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    return entries


def _validate_manifest(data: Any, source: str = "更新清单") -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"不支持的更新清单: {source}")
    files = data.get("files")
    if not isinstance(files, list):
        raise ValueError("更新清单缺少 files 列表")
    seen: set[str] = set()
    normalized_files: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("更新清单中的文件项无效")
        relative = _safe_relative_path(str(item.get("path", "")))
        if relative in seen:
            raise ValueError(f"更新清单中存在重复文件: {relative}")
        seen.add(relative)
        if not isinstance(item.get("size"), int) or item["size"] < 0:
            raise ValueError(f"更新清单中的文件大小无效: {relative}")
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise ValueError(f"更新清单中的文件校验值无效: {relative}")
        normalized_files.append({"path": relative, "size": item["size"], "sha256": digest.lower()})
    data["files"] = normalized_files
    for list_name in ("changed_files", "deleted_files"):
        listed = data.get(list_name, [])
        if not isinstance(listed, list) or any(not isinstance(item, str) for item in listed):
            raise ValueError(f"更新清单中的 {list_name} 无效")
        normalized = [_safe_relative_path(item) for item in listed]
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"更新清单中的 {list_name} 存在重复文件")
        data[list_name] = normalized
    if set(data["changed_files"]) & set(data["deleted_files"]):
        raise ValueError("更新清单同时修改和删除了同一个文件")
    if any(item not in seen for item in data["changed_files"]):
        raise ValueError("更新清单的 changed_files 未出现在 files 列表中")
    return data


def _read_manifest(path: Path) -> dict[str, Any]:
    return _validate_manifest(json.loads(path.read_text(encoding="utf-8")), str(path))


def create_update_package(
    root: Path,
    *,
    version: str,
    output: Path,
    manifest_output: Path,
    previous_manifest: Path | None = None,
    package_base_url: str | None = None,
) -> dict[str, Any]:
    """Create a full first-release or changed-files-only update archive."""
    root = root.resolve()
    current_files = _managed_files(root)
    previous: dict[str, Any] = {}
    if previous_manifest and previous_manifest.is_file():
        previous = _read_manifest(previous_manifest)
    previous_by_path = {item["path"]: item for item in previous.get("files", [])}
    current_by_path = {item["path"]: item for item in current_files}

    changed = [
        item["path"]
        for item in current_files
        if not previous_by_path or previous_by_path.get(item["path"]) != item
    ]
    deleted = sorted(set(previous_by_path) - set(current_by_path))
    output = output.resolve()
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "version": version,
        "base_version": previous.get("version"),
        "package": output.name,
        "package_url": f"{package_base_url.rstrip('/')}/{output.name}" if package_base_url else None,
        "files": current_files,
        "changed_files": sorted(changed),
        "deleted_files": deleted,
    }
    manifest_output = manifest_output.resolve()
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    manifest_output.write_text(manifest_text, encoding="utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(MANIFEST_NAME, manifest_text)
        for relative in sorted(changed):
            source = root / Path(*relative.split("/"))
            if not source.is_file():
                raise FileNotFoundError(source)
            archive.write(source, relative)
    return manifest


def _manifest_files_by_path(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["path"]): item for item in manifest["files"]}


def _zip_member_path(name: str) -> str:
    return _safe_relative_path(name)


def _install_path(install_root: Path, relative: str) -> Path:
    """Resolve a managed path without following symlinks/junctions in the install tree."""
    target = install_root / Path(*relative.split("/"))
    current = install_root
    for part in relative.split("/"):
        current /= part
        if current.is_symlink():
            raise ValueError(f"更新目标包含符号链接，已拒绝: {relative}")
    try:
        target.resolve().relative_to(install_root.resolve())
    except ValueError as error:
        raise ValueError(f"更新目标超出安装目录: {relative}") from error
    return target


def _wait_for_pid(pid: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return
        time.sleep(0.25)
    raise TimeoutError(f"等待进程退出超时: {pid}")


def apply_update(
    install_root: Path,
    package: Path,
    *,
    wait_for_pid: int | None = None,
    wait_timeout: float = 120.0,
) -> dict[str, Any]:
    """Apply a validated delta archive, preserving unmanaged files on failure."""
    install_root = install_root.resolve()
    install_root.mkdir(parents=True, exist_ok=True)
    if wait_for_pid is not None:
        _wait_for_pid(wait_for_pid, wait_timeout)
    with zipfile.ZipFile(package) as archive:
        try:
            manifest_bytes = archive.read(MANIFEST_NAME)
        except KeyError as error:
            raise ValueError("更新包缺少 update-manifest.json") from error
        manifest = _validate_manifest(json.loads(manifest_bytes.decode("utf-8")), f"{package}:{MANIFEST_NAME}")
        local_manifest = install_root / MANIFEST_NAME
        if manifest.get("base_version"):
            if not local_manifest.is_file():
                raise ValueError("当前安装没有版本清单，请先使用完整安装包安装")
            installed_version = _read_manifest(local_manifest).get("version")
            if installed_version != manifest["base_version"]:
                raise ValueError(
                    f"更新包基于 v{manifest['base_version']}，当前安装是 v{installed_version or 'unknown'}；请先更新到对应版本"
                )
        files_by_path = _manifest_files_by_path(manifest)
        changed = list(manifest["changed_files"])
        deleted = list(manifest["deleted_files"])
        members = {_zip_member_path(info.filename): info for info in archive.infolist() if not info.is_dir()}
        for relative in changed:
            if relative not in members or relative not in files_by_path:
                raise ValueError(f"更新包缺少文件: {relative}")
        with tempfile.TemporaryDirectory(prefix="testbox-update-") as staging_name:
            staging = Path(staging_name)
            staged: dict[str, Path] = {}
            for relative in changed:
                target = staging / Path(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(members[relative]) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                expected = files_by_path[relative]
                if target.stat().st_size != expected["size"] or sha256_file(target) != expected["sha256"]:
                    raise ValueError(f"更新文件校验失败: {relative}")
                staged[relative] = target

            backup = Path(tempfile.mkdtemp(prefix="testbox-update-backup-"))
            old_manifest = install_root / MANIFEST_NAME
            old_manifest_backup = backup / MANIFEST_NAME
            had_files: set[str] = set()
            try:
                for relative in sorted(set(changed + deleted)):
                    target = _install_path(install_root, relative)
                    if target.is_file():
                        backup_target = backup / Path(*relative.split("/"))
                        backup_target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup_target)
                        had_files.add(relative)
                if old_manifest.is_file():
                    shutil.copy2(old_manifest, old_manifest_backup)

                for relative, source in staged.items():
                    target = _install_path(install_root, relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(f".{target.name}.testbox-update.tmp")
                    shutil.copy2(source, temporary)
                    temporary.replace(target)
                for relative in deleted:
                    target = _install_path(install_root, relative)
                    if target.is_file():
                        target.unlink()
                temporary_manifest = install_root / f".{MANIFEST_NAME}.testbox-update.tmp"
                temporary_manifest.write_bytes(manifest_bytes)
                temporary_manifest.replace(old_manifest)
            except Exception:
                for relative in changed + deleted:
                    target = _install_path(install_root, relative)
                    backup_target = backup / Path(*relative.split("/"))
                    if backup_target.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup_target, target)
                    elif relative not in had_files and target.exists():
                        target.unlink()
                if old_manifest_backup.is_file():
                    shutil.copy2(old_manifest_backup, old_manifest)
                raise
            finally:
                shutil.rmtree(backup, ignore_errors=True)
    return {"version": manifest["version"], "changed": changed, "deleted": deleted}


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "TestBox-Updater"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as stream:
        shutil.copyfileobj(response, stream)


def download_and_apply(
    install_root: Path,
    *,
    manifest_url: str,
    wait_for_pid: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="testbox-download-") as temporary_name:
        temporary = Path(temporary_name)
        manifest_path = temporary / MANIFEST_NAME
        download(manifest_url, manifest_path)
        manifest = _read_manifest(manifest_path)
        local_manifest_path = install_root / MANIFEST_NAME
        local_version = None
        if local_manifest_path.is_file():
            local_version = _read_manifest(local_manifest_path).get("version")
        if local_version == manifest.get("version") and not force:
            return {"status": "up_to_date", "version": local_version}
        package_url = manifest.get("package_url")
        if not package_url:
            raise ValueError("更新清单缺少 package_url")
        package_path = temporary / str(manifest["package"])
        download(package_url, package_path)
        result = apply_update(install_root, package_path, wait_for_pid=wait_for_pid)
        result["status"] = "updated"
        result["from_version"] = local_version
        return result
