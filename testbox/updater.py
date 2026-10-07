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
import stat
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_NAME = "update-manifest.json"
UPDATER_NAMES = frozenset({"TestBox-Updater.exe", "TestBox-CLI-Updater.exe", "TestBox-GUI-Updater.exe"})
MANIFEST_SCHEMA_VERSION = 2
VALID_COMPONENTS = frozenset({"cli", "gui", "test"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(path: str) -> str:
    # Use Windows rules even when release packages are assembled/tested elsewhere.
    reserved = {"con", "prn", "aux", "nul", "conin$", "conout$"}
    reserved.update(f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10))
    if not isinstance(path, str) or not path or "\\" in path:
        raise ValueError(f"非法更新文件路径: {path}")
    parts = path.split("/")
    if any(not part or part in (".", "..") or part.endswith((".", " "))
           or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
           or part.split(".")[0].casefold() in reserved for part in parts):
        raise ValueError(f"非法更新文件路径: {path}")
    return PurePosixPath(path).as_posix()


def _managed_relative_path(path: str) -> str:
    relative = _safe_relative_path(path)
    if relative.casefold() == MANIFEST_NAME.casefold() or relative.split("/")[-1].casefold() in {
        name.casefold() for name in UPDATER_NAMES
    }:
        raise ValueError(f"更新清单试图管理受保护文件: {relative}")
    return relative


def _managed_files(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if _is_redirect(path):
            raise ValueError(f"发布目录包含符号链接或 junction: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.casefold() == MANIFEST_NAME.casefold() or path.name.casefold() in {name.casefold() for name in UPDATER_NAMES}:
            continue
        relative = _managed_relative_path(relative)
        entries.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    return entries


def _validate_manifest(data: Any, source: str = "更新清单") -> dict[str, Any]:
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"不支持的更新清单: {source}")
    component = data.get("component")
    if not isinstance(component, str) or component not in VALID_COMPONENTS:
        raise ValueError("更新清单中的 component 无效")
    data = dict(data)
    if not isinstance(data.get("version"), str) or not data["version"].strip():
        raise ValueError("更新清单中的 version 无效")
    if data.get("base_version") is not None and (not isinstance(data["base_version"], str) or not data["base_version"].strip()):
        raise ValueError("更新清单中的 base_version 无效")
    if "package" in data:
        package = _safe_relative_path(data["package"])
        if "/" in package:
            raise ValueError("非法更新包文件名")
    files = data.get("files")
    if not isinstance(files, list):
        raise ValueError("更新清单缺少 files 列表")
    seen: set[str] = set()
    normalized_files: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("更新清单中的文件项无效")
        relative = _managed_relative_path(item.get("path", ""))
        if relative.casefold() in seen:
            raise ValueError(f"更新清单中存在重复文件: {relative}")
        seen.add(relative.casefold())
        if type(item.get("size")) is not int or item["size"] < 0:
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
        normalized = [_managed_relative_path(item) for item in listed]
        if len(normalized) != len({item.casefold() for item in normalized}):
            raise ValueError(f"更新清单中的 {list_name} 存在重复文件")
        data[list_name] = normalized
    if set(data["changed_files"]) & set(data["deleted_files"]):
        raise ValueError("更新清单同时修改和删除了同一个文件")
    if any(item not in {entry["path"] for entry in normalized_files} for item in data["changed_files"]):
        raise ValueError("更新清单的 changed_files 未出现在 files 列表中")
    if {item.casefold() for item in data["deleted_files"]} & seen:
        raise ValueError("更新清单不能删除目标版本登记的文件")
    for relative in seen:
        parts = relative.split("/")
        if any("/".join(parts[:index]) in seen for index in range(1, len(parts))):
            raise ValueError("更新清单存在文件/目录路径冲突")
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
    component: str = "test",
) -> dict[str, Any]:
    """Create a full first-release or changed-files-only update archive."""
    if not isinstance(component, str) or component not in VALID_COMPONENTS:
        raise ValueError(f"不支持的更新组件: {component}")
    root = root.resolve()
    current_files = _managed_files(root)
    previous: dict[str, Any] = {}
    if previous_manifest and previous_manifest.is_file():
        previous = _read_manifest(previous_manifest)
        if previous["component"] != component:
            raise ValueError(
                f"更新清单组件不匹配: {previous['component']} 不能用于 {component}"
            )
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
        "component": component,
        "version": version,
        "base_version": previous.get("version"),
        "package": output.name,
        "package_url": f"{package_base_url.rstrip('/')}/{output.name}" if package_base_url else None,
        "files": current_files,
        "changed_files": sorted(changed),
        "deleted_files": deleted,
    }
    manifest = _validate_manifest(manifest)
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


def _is_redirect(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _install_path(install_root: Path, relative: str) -> Path:
    """Resolve a managed path without following symlinks/junctions in the install tree."""
    target = install_root / Path(*relative.split("/"))
    current = install_root
    for part in relative.split("/"):
        current /= part
        if _is_redirect(current):
            raise ValueError(f"更新目标包含符号链接，已拒绝: {relative}")
    try:
        target.resolve().relative_to(install_root.resolve())
    except ValueError as error:
        raise ValueError(f"更新目标超出安装目录: {relative}") from error
    return target


def _wait_for_windows_pid(pid: int, timeout: float) -> None:
    # os.kill(pid, 0) on Windows calls TerminateProcess: never use it here.
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: process no longer exists
            return
        raise ctypes.WinError(error)
    try:
        status = kernel32.WaitForSingleObject(handle, min(int(timeout * 1000), 0xFFFFFFFE))
        if status == 0x102:  # WAIT_TIMEOUT
            raise TimeoutError(f"等待进程退出超时: {pid}")
        if status != 0:  # WAIT_OBJECT_0
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def _wait_for_pid(pid: int, timeout: float) -> None:
    if pid <= 0 or timeout < 0:
        raise ValueError("等待进程的 PID 必须为正数，超时不能为负数")
    if os.name == "nt":
        return _wait_for_windows_pid(pid, timeout)
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        # Permission errors must not be interpreted as process exit.
        if time.monotonic() >= deadline:
            raise TimeoutError(f"等待进程退出超时: {pid}")
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def apply_update(
    install_root: Path,
    package: Path,
    *,
    wait_for_pid: int | None = None,
    wait_timeout: float = 120.0,
    expected_component: str | None = None,
    expected_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate before mutation; preserve recovery material if rollback fails."""
    if _is_redirect(install_root):
        raise ValueError("更新安装目录包含符号链接或 junction，已拒绝")
    install_root = install_root.resolve()
    with zipfile.ZipFile(package) as archive:
        members: dict[str, zipfile.ZipInfo] = {}
        seen: set[str] = set()
        for info in archive.infolist():
            relative = _zip_member_path(info.filename.rstrip("/") if info.is_dir() else info.filename)
            if relative.casefold() in seen:
                raise ValueError(f"更新包存在重复路径: {relative}")
            seen.add(relative.casefold())
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR and not info.is_dir()):
                raise ValueError(f"更新包包含链接或特殊文件: {relative}")
            if not info.is_dir():
                members[relative] = info
        if MANIFEST_NAME not in members:
            raise ValueError("更新包缺少 update-manifest.json")
        if members[MANIFEST_NAME].file_size > 8 * 1024 * 1024:
            raise ValueError("更新清单过大")
        manifest_bytes = archive.read(members[MANIFEST_NAME])
        manifest = _validate_manifest(json.loads(manifest_bytes.decode("utf-8")), f"{package}:{MANIFEST_NAME}")
        if expected_component is not None and manifest["component"] != expected_component:
            raise ValueError(f"更新组件不匹配：更新器是 {expected_component}，更新包是 {manifest['component']}")
        if expected_manifest is not None and manifest != _validate_manifest(expected_manifest):
            raise ValueError("下载清单与更新包内清单不一致")
        files_by_path = _manifest_files_by_path(manifest)
        changed = list(manifest["changed_files"])
        deleted = list(manifest["deleted_files"])
        if set(members) - {MANIFEST_NAME} != set(changed):
            raise ValueError("更新包文件缺失或包含未登记文件")

        with tempfile.TemporaryDirectory(prefix="testbox-update-") as staging_name:
            staging = Path(staging_name)
            staged: dict[str, Path] = {}
            for relative in changed:
                expected = files_by_path[relative]
                if members[relative].file_size != expected["size"]:
                    raise ValueError(f"更新文件大小校验失败: {relative}")
                target = staging / Path(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                remaining = expected["size"]
                with archive.open(members[relative]) as source, target.open("wb") as destination:
                    while True:
                        chunk = source.read(min(1024 * 1024, remaining + 1))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise ValueError(f"更新文件大小校验失败: {relative}")
                        destination.write(chunk)
                if remaining or sha256_file(target) != expected["sha256"]:
                    raise ValueError(f"更新文件校验失败: {relative}")
                staged[relative] = target

            if wait_for_pid is not None:
                _wait_for_pid(wait_for_pid, wait_timeout)
            old_manifest = _install_path(install_root, MANIFEST_NAME)
            installed = _read_manifest(old_manifest) if old_manifest.is_file() else None
            if installed and installed["component"] != manifest["component"]:
                raise ValueError(f"更新组件不匹配：安装的是 {installed['component']}，更新包是 {manifest['component']}")
            if manifest.get("base_version"):
                if installed is None:
                    raise ValueError("当前安装没有版本清单，请先使用完整安装包安装")
                if installed["version"] != manifest["base_version"]:
                    raise ValueError(f"更新包基于 v{manifest['base_version']}，当前安装是 v{installed['version']}；请先更新到对应版本")
            old_files = _manifest_files_by_path(installed) if installed else {}
            if any(relative not in old_files for relative in deleted):
                raise ValueError("更新包试图删除未登记的用户文件")
            if manifest.get("base_version") and set(deleted) != set(old_files) - set(files_by_path):
                raise ValueError("增量清单的删除文件与上一版本不一致")
            for relative in changed + deleted:
                target = _install_path(install_root, relative)
                if target.exists() and (not target.is_file() or relative not in old_files):
                    raise ValueError(f"更新目标冲突或试图覆盖未登记的用户文件: {relative}")
            # The delta must lead to the advertised complete target, not merely
            # patch some files and stamp a new version onto an incomplete install.
            for relative, expected in files_by_path.items():
                if relative in staged:
                    continue
                target = _install_path(install_root, relative)
                if not target.is_file() or target.stat().st_size != expected["size"] or sha256_file(target) != expected["sha256"]:
                    raise ValueError(f"未变化文件校验失败，请使用完整安装包修复: {relative}")

            install_root.mkdir(parents=True, exist_ok=True)
            backup = Path(tempfile.mkdtemp(prefix="testbox-update-backup-", dir=install_root.parent))
            old_manifest_backup = backup / MANIFEST_NAME
            touched: list[str] = []
            temporaries: list[Path] = []
            created_dirs: list[Path] = []
            preserve_backup = False

            def make_parents(target: Path) -> None:
                missing: list[Path] = []
                current = target.parent
                while current != install_root and not current.exists():
                    missing.append(current)
                    current = current.parent
                for directory in reversed(missing):
                    directory.mkdir()
                    created_dirs.append(directory)

            def temporary_for(target: Path) -> Path:
                fd, name = tempfile.mkstemp(prefix=f".{target.name}.testbox-update-", dir=target.parent)
                os.close(fd)
                temporary = Path(name)
                temporaries.append(temporary)
                return temporary

            try:
                # Finish ALL backups before the first mutation. Backup failure
                # cannot trigger deletion of files whose backup was not reached.
                for relative in sorted(set(changed + deleted)):
                    target = _install_path(install_root, relative)
                    if target.is_file():
                        backup_target = backup / Path(*relative.split("/"))
                        backup_target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup_target)
                if old_manifest.is_file():
                    shutil.copy2(old_manifest, old_manifest_backup)

                for relative, source in staged.items():
                    target = _install_path(install_root, relative)
                    make_parents(target)
                    temporary = temporary_for(target)
                    shutil.copy2(source, temporary)
                    temporary.replace(target)
                    touched.append(relative)
                for relative in deleted:
                    target = _install_path(install_root, relative)
                    if target.is_file():
                        target.unlink()
                        touched.append(relative)
                temporary_manifest = temporary_for(old_manifest)
                temporary_manifest.write_bytes(manifest_bytes)
                temporary_manifest.replace(old_manifest)
            except Exception as error:
                recovery_errors: list[str] = []
                for relative in reversed(touched):
                    try:
                        target = _install_path(install_root, relative)
                        backup_target = backup / Path(*relative.split("/"))
                        if backup_target.is_file():
                            shutil.copy2(backup_target, target)
                        elif target.exists():
                            target.unlink()
                    except Exception as recovery_error:
                        recovery_errors.append(f"{relative}: {recovery_error}")
                # The manifest is committed last, so it remains unchanged on
                # failure; do not risk damaging it with an unnecessary rewrite.
                for temporary in temporaries:
                    try:
                        temporary.unlink(missing_ok=True)
                    except Exception as recovery_error:
                        recovery_errors.append(f"{temporary}: {recovery_error}")
                for directory in reversed(created_dirs):
                    try:
                        directory.rmdir()
                    except Exception as recovery_error:
                        recovery_errors.append(f"{directory}: {recovery_error}")
                if recovery_errors:
                    preserve_backup = True
                    raise RuntimeError(f"更新失败: {error}；回滚失败，备份保留在 {backup}；" + "; ".join(recovery_errors)) from error
                raise
            finally:
                if not preserve_backup:
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
    expected_component: str | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="testbox-download-") as temporary_name:
        temporary = Path(temporary_name)
        manifest_path = temporary / MANIFEST_NAME
        download(manifest_url, manifest_path)
        manifest = _read_manifest(manifest_path)
        if expected_component is not None and manifest["component"] != expected_component:
            raise ValueError("更新组件不匹配：下载清单不属于当前更新器")
        local_manifest_path = _install_path(install_root.resolve(), MANIFEST_NAME)
        local_version = None
        if local_manifest_path.is_file():
            local_manifest = _read_manifest(local_manifest_path)
            if local_manifest["component"] != manifest["component"]:
                raise ValueError(
                    f"更新组件不匹配：安装的是 {local_manifest['component']}，更新清单是 {manifest['component']}"
                )
            local_version = local_manifest.get("version")
        if local_version == manifest.get("version") and not force:
            return {"status": "up_to_date", "version": local_version}
        package_url = manifest.get("package_url")
        if not package_url:
            raise ValueError("更新清单缺少 package_url")
        package_name = _safe_relative_path(manifest.get("package", ""))
        if "/" in package_name:
            raise ValueError("非法更新包文件名")
        package_path = temporary / package_name
        download(package_url, package_path)
        result = apply_update(install_root, package_path, wait_for_pid=wait_for_pid,
                              expected_component=expected_component, expected_manifest=manifest)
        result["status"] = "updated"
        result["from_version"] = local_version
        return result
