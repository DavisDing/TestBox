from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMMAND_RE = re.compile(r"^[a-z0-9]+(?:\.[a-z0-9]+)+$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "false"}: return value == "true"
    if value in {"[]", "{}"}: return [] if value == "[]" else {}
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def load_yaml_subset(path: Path) -> dict[str, Any]:
    """Read the constrained YAML shape used by TestBox manifests without a runtime dependency."""
    root: dict[str, Any] = {}; current_list: list[dict[str, Any]] | None = None; current_item: dict[str, Any] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip(): continue
        indent, text = len(line) - len(line.lstrip()), line.strip()
        if indent == 0 and ":" in text:
            key, value = (part.strip() for part in text.split(":", 1))
            if value:
                root[key] = _scalar(value); current_list = None
            else:
                root[key] = []; current_list = root[key]
        elif indent == 2 and text.startswith("- ") and current_list is not None:
            key, value = (part.strip() for part in text[2:].split(":", 1)); current_item = {key: _scalar(value)}; current_list.append(current_item)
        elif indent >= 4 and ":" in text and current_item is not None:
            key, value = (part.strip() for part in text.split(":", 1)); current_item[key] = _scalar(value)
    return root


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    input_schema: str | None = None


@dataclass(frozen=True)
class Manifest:
    path: Path
    name: str
    version: str
    description: str
    entry: str
    commands: list[Command]
    capabilities: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        raw = load_yaml_subset(path)
        required = ("schema_version", "name", "version", "description", "entry", "commands")
        missing = [key for key in required if key not in raw]
        if missing: raise ValueError(f"manifest 缺少字段: {', '.join(missing)}")
        if raw["schema_version"] != "1" and raw["schema_version"] != 1: raise ValueError("仅支持 schema_version: 1")
        if not NAME_RE.fullmatch(str(raw["name"])): raise ValueError("插件 name 必须为小写字母、数字和连字符")
        if not VERSION_RE.fullmatch(str(raw["version"])): raise ValueError("插件 version 必须为语义化版本")
        if ":" not in str(raw["entry"]): raise ValueError("entry 必须为 module:Class")
        commands = [Command(str(item.get("name", "")), str(item.get("description", "")), item.get("input_schema")) for item in raw["commands"]]
        if not commands or any(not COMMAND_RE.fullmatch(command.name) for command in commands): raise ValueError("commands 必须包含小写点分命令")
        return cls(path.parent, str(raw["name"]), str(raw["version"]), str(raw["description"]), str(raw["entry"]), commands, raw.get("capabilities", {}))
