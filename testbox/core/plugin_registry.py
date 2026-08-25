"""Plugin discovery and command registry."""
from __future__ import annotations

from pathlib import Path

from testbox.core.manifest import Manifest


class PluginManager:
    def __init__(self, plugins_dirs: list[Path]):
        self.plugins_dirs = plugins_dirs
        self.available: dict[str, Manifest] = {}
        self.unavailable: dict[str, str] = {}

    def discover(self) -> None:
        self.available.clear()
        self.unavailable.clear()
        seen_commands: dict[str, Manifest] = {}
        for plugins_dir in self.plugins_dirs:
            if not plugins_dir.exists():
                continue
            for manifest_path in sorted(plugins_dir.glob("*/manifest.yaml")):
                try:
                    manifest = Manifest.load(manifest_path)
                    command_names = [command.name for command in manifest.commands]
                    if len(command_names) != len(set(command_names)):
                        raise ValueError("插件内命令重复")
                    conflicts = [
                        command.name
                        for command in manifest.commands
                        if command.name in seen_commands and seen_commands[command.name].name != manifest.name
                    ]
                    if conflicts:
                        previous = seen_commands[conflicts[0]]
                        raise ValueError(f"命令冲突: {', '.join(conflicts)}（已有插件: {previous.name}）")
                    for command in manifest.commands:
                        # User-installed plugin directories are searched before
                        # bundled plugins. A bundled copy of the same plugin is
                        # intentionally ignored so upgrades take precedence.
                        if command.name in seen_commands:
                            continue
                        seen_commands[command.name] = manifest
                        self.available[command.name] = manifest
                except Exception as error:
                    self.unavailable[str(manifest_path.parent)] = str(error)

    def get_manifest(self, command: str) -> Manifest:
        try:
            return self.available[command]
        except KeyError as error:
            raise LookupError(f"未找到可用命令: {command}") from error
