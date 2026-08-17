"""Configuration loading and precedence for the local TestBox runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from testbox.core.manifest import load_yaml_subset


def _mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = load_yaml_subset(path)
    if not isinstance(value, dict):
        raise ValueError(f"配置文件必须是对象: {path}")
    return value


def _environment(plugin_name: str) -> dict[str, Any]:
    prefix = f"TESTBOX_{plugin_name.upper().replace('-', '_')}_"
    result: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        name = key.removeprefix(prefix).lower()
        try:
            result[name] = json.loads(value)
        except json.JSONDecodeError:
            result[name] = value
    return result


def load_plugin_config(root: Path, plugin_path: Path, plugin_name: str) -> dict[str, Any]:
    """Merge global, project, plugin and environment settings in documented order."""
    config: dict[str, Any] = {}
    for source in (Path.home() / ".testbox" / "config.yaml", plugin_path / "config" / "config.yaml", root / "config.yaml"):
        config.update(_mapping(source))
    config.update(_environment(plugin_name))
    return config
