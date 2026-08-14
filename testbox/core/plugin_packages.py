"""Safe local plugin archive creation, installation and removal."""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from testbox.core.manifest import Manifest


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


def _unpack_archive(archive: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as package:
            for info in package.infolist():
                target = (destination / info.filename).resolve()
                if destination.resolve() not in target.parents and target != destination.resolve():
                    raise PluginPackageError("插件包包含非法路径")
            package.extractall(destination)
    except zipfile.BadZipFile as error:
        raise PluginPackageError("插件包不是有效 ZIP 文件") from error


def install_plugin(source: Path, plugins_dir: Path, *, force: bool = False) -> Manifest:
    """Validate in a temporary directory, then atomically enable the plugin."""
    with tempfile.TemporaryDirectory(prefix="testbox-plugin-") as temporary:
        staging = Path(temporary) / "package"; staging.mkdir()
        if source.is_dir():
            shutil.copytree(source, staging, dirs_exist_ok=True)
        elif source.is_file():
            _unpack_archive(source, staging)
        else:
            raise PluginPackageError("插件路径不存在")
        manifest = Manifest.load(staging / "manifest.yaml")
        target = plugins_dir / manifest.name
        if target.exists() and not force:
            raise PluginPackageError(f"插件已安装: {manifest.name}（使用 --force 覆盖）")
        plugins_dir.mkdir(parents=True, exist_ok=True)
        replacement = plugins_dir / f".{manifest.name}.installing"
        if replacement.exists(): shutil.rmtree(replacement)
        shutil.copytree(staging, replacement)
        if target.exists():
            backup = plugins_dir / f".{manifest.name}.previous"
            if backup.exists(): shutil.rmtree(backup)
            target.replace(backup); replacement.replace(target); shutil.rmtree(backup)
        else: replacement.replace(target)
        return Manifest.load(target / "manifest.yaml")


def uninstall_plugin(name: str, plugins_dir: Path) -> None:
    target = plugins_dir / name
    if not target.is_dir(): raise PluginPackageError(f"未安装插件: {name}")
    manifest = Manifest.load(target / "manifest.yaml")
    if manifest.name != name: raise PluginPackageError("插件目录与清单名称不一致，拒绝卸载")
    shutil.rmtree(target)
