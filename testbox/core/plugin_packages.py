"""Safe local plugin archive creation, installation and removal."""
from __future__ import annotations

import shutil
import stat
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from testbox.core.manifest import Manifest


# Quotas apply both to ZIP metadata and to bytes actually read from each member.
MAX_ARCHIVE_ENTRIES = 4096
MAX_ARCHIVE_FILE_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
ARCHIVE_CHUNK_BYTES = 1024 * 1024
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{suffix}" for prefix in ("COM", "LPT") for suffix in "123456789¹²³"
}


class PluginPackageError(ValueError):
    pass


def package_plugin(source: Path, destination: Path) -> Path:
    manifest = Manifest.load(source / "manifest.yaml")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in source.rglob("*"):
            if item.is_file() and "__pycache__" not in item.parts and not item.name.endswith((".pyc", ".pyo")):
                archive.write(item, item.relative_to(source).as_posix())
    return destination


def _archive_relative_path(info: zipfile.ZipInfo) -> Path:
    # Treat both separators as separators even when inspecting on Unix. ZIP
    # names must be safe when this same package is later installed on Windows.
    name = info.orig_filename.replace("\\", "/")
    windows_path = PureWindowsPath(name)
    parts = name.rstrip("/").split("/")
    if (
        "\x00" in name or name.startswith("/") or windows_path.drive
        or any(part in {"", ".", ".."} for part in parts)
        or any(
            any(char in part for char in ':<>"|?*')
            or any(ord(char) < 32 for char in part)
            or part.endswith((".", " "))
            for part in parts
        )
        or any(part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES for part in parts)
    ):
        raise PluginPackageError(f"插件包包含非法路径: {info.orig_filename!r}")
    # Unix mode bits encode symlinks and special files. DOS reparse points
    # represent links on Windows; accept only ordinary files/directories.
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or info.external_attr & 0x400:
        raise PluginPackageError(f"插件包包含链接或特殊文件: {info.filename}")
    if kind == stat.S_IFDIR and not name.endswith("/"):
        raise PluginPackageError(f"插件包目录标记无效: {info.filename}")
    return Path(*parts)


def _unpack_archive(archive: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as package:
            entries = package.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise PluginPackageError(f"插件包条目数超过 {MAX_ARCHIVE_ENTRIES} 限制")
            declared_total = 0
            members = []
            names: set[str] = set()
            # Validate the entire directory before writing any member.
            for info in entries:
                relative = _archive_relative_path(info)
                canonical = relative.as_posix().casefold()
                if canonical in names:
                    raise PluginPackageError(f"插件包包含重复路径: {info.filename}")
                names.add(canonical)
                if info.file_size < 0 or info.file_size > MAX_ARCHIVE_FILE_BYTES:
                    raise PluginPackageError(f"插件包单项大小超过 {MAX_ARCHIVE_FILE_BYTES} 字节限制")
                declared_total += info.file_size
                if declared_total > MAX_ARCHIVE_TOTAL_BYTES:
                    raise PluginPackageError(f"插件包总大小超过 {MAX_ARCHIVE_TOTAL_BYTES} 字节限制")
                is_directory = info.orig_filename.endswith(("/", "\\"))
                if is_directory and info.file_size:
                    raise PluginPackageError("插件包目录条目不能包含数据")
                members.append((info, relative, is_directory))

            root = destination.resolve()
            actual_total = 0
            for info, relative, is_directory in members:
                target = destination / relative
                try:
                    target.resolve().relative_to(root)
                except ValueError as error:
                    raise PluginPackageError("插件包包含非法路径") from error
                if is_directory:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                actual_size = 0
                with package.open(info) as source, target.open("xb") as output:
                    while True:
                        remaining = min(
                            MAX_ARCHIVE_FILE_BYTES - actual_size,
                            MAX_ARCHIVE_TOTAL_BYTES - actual_total,
                        )
                        chunk = source.read(min(ARCHIVE_CHUNK_BYTES, remaining + 1))
                        if not chunk:
                            break
                        actual_size += len(chunk)
                        actual_total += len(chunk)
                        if actual_size > MAX_ARCHIVE_FILE_BYTES or actual_total > MAX_ARCHIVE_TOTAL_BYTES:
                            raise PluginPackageError("插件包实际解压大小超过限制")
                        if actual_size > info.file_size:
                            raise PluginPackageError("插件包实际解压大小与声明不符")
                        output.write(chunk)
                if actual_size != info.file_size:
                    raise PluginPackageError("插件包实际解压大小与声明不符")
    except zipfile.BadZipFile as error:
        raise PluginPackageError("插件包不是有效 ZIP 文件") from error
    except (OSError, RuntimeError, NotImplementedError) as error:
        raise PluginPackageError(f"插件包解压失败: {error}") from error


def _stage_plugin_source(source: Path, staging: Path) -> Manifest:
    if source.is_dir():
        shutil.copytree(source, staging, dirs_exist_ok=True)
    elif source.is_file():
        _unpack_archive(source, staging)
    else:
        raise PluginPackageError("插件路径不存在")
    return Manifest.load(staging / "manifest.yaml")


def inspect_plugin(source: Path) -> Manifest:
    """Validate a plugin directory or ZIP without installing it."""
    with tempfile.TemporaryDirectory(prefix="testbox-plugin-inspect-") as temporary:
        staging = Path(temporary) / "package"
        staging.mkdir()
        return _stage_plugin_source(source, staging)


def install_plugin(
    source: Path,
    plugins_dir: Path,
    *,
    force: bool = False,
    validate: Callable[[Manifest], None] | None = None,
) -> Manifest:
    """Validate in a temporary directory, then atomically enable the plugin."""
    with tempfile.TemporaryDirectory(prefix="testbox-plugin-") as temporary:
        staging = Path(temporary) / "package"; staging.mkdir()
        manifest = _stage_plugin_source(source, staging)
        if validate is not None:
            validate(manifest)
        target = plugins_dir / manifest.name
        if target.exists() and not force:
            raise PluginPackageError(f"插件已安装: {manifest.name}（使用 --force 覆盖）")
        plugins_dir.mkdir(parents=True, exist_ok=True)
        # Unique siblings keep recoverable data from an earlier failed install
        # intact, and keep every rename on the same filesystem.
        token = uuid4().hex
        replacement = plugins_dir / f".{manifest.name}.installing-{token}"
        backup = plugins_dir / f".{manifest.name}.previous-{token}"
        try:
            shutil.copytree(staging, replacement)
        except Exception:
            shutil.rmtree(replacement, ignore_errors=True)
            raise
        backed_up = False
        try:
            if target.exists():
                target.replace(backup)
                backed_up = True
            replacement.replace(target)
            installed = Manifest.load(target / "manifest.yaml")
        except Exception as activation_error:
            if backed_up:
                try:
                    # Post-activation validation can fail too. Move the new
                    # directory aside before restoring the previous version.
                    if target.exists():
                        target.replace(replacement)
                    backup.replace(target)
                except Exception as rollback_error:
                    raise PluginPackageError(
                        f"插件激活失败: {activation_error}; 回滚失败: {rollback_error}; "
                        f"旧插件保留于 {backup}; 新插件保留于 {replacement} 或 {target}，请手动恢复"
                    ) from activation_error
            elif target.exists() and not replacement.exists():
                # No old installation existed; preserve the rejected new one.
                try:
                    target.replace(replacement)
                except OSError as recovery_error:
                    raise PluginPackageError(
                        f"插件激活失败: {activation_error}; 新插件移出失败: {recovery_error}; "
                        f"新插件保留于 {target}，请手动恢复"
                    ) from activation_error
            if backed_up or target.exists():
                shutil.rmtree(replacement, ignore_errors=True)
            raise PluginPackageError(f"插件激活失败: {activation_error}") from activation_error
        if backed_up:
            try:
                shutil.rmtree(backup)
            except OSError as error:
                raise PluginPackageError(f"插件已激活，但旧备份清理失败，保留于 {backup}: {error}") from error
        return installed


def uninstall_plugin(name: str, plugins_dir: Path) -> None:
    target = plugins_dir / name
    if not target.is_dir(): raise PluginPackageError(f"未安装插件: {name}")
    manifest = Manifest.load(target / "manifest.yaml")
    if manifest.name != name: raise PluginPackageError("插件目录与清单名称不一致，拒绝卸载")
    shutil.rmtree(target)
