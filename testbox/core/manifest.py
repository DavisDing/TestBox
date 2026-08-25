from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMMAND_RE = re.compile(r"^[a-z0-9]+(?:\.[a-z0-9]+)+$")
ENTRY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("版本必须为语义化版本")
    return tuple(map(int, match.groups()))


def _core_compatible(requirement: str) -> bool:
    from testbox import __version__

    current = _version_tuple(__version__)
    try:
        constraints = [item.strip() for item in requirement.split(",") if item.strip()]
        for constraint in constraints:
            match = re.fullmatch(r"(>=|<=|>|<|==)?(\d+\.\d+(?:\.\d+)?)", constraint)
            if not match:
                return False
            operator, version = match.groups()
            parts = tuple(map(int, version.split(".")))
            expected = parts + (0,) * (3 - len(parts))
            if operator in (None, "==") and current != expected: return False
            if operator == ">=" and current < expected: return False
            if operator == "<=" and current > expected: return False
            if operator == ">" and current <= expected: return False
            if operator == "<" and current >= expected: return False
    except ValueError:
        return False
    return bool(constraints)


def _scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "false"}: return value == "true"
    if value in {"[]", "{}"}: return [] if value == "[]" else {}
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def load_yaml_subset(path: Path) -> dict[str, Any]:
    """Load the constrained manifest YAML, preferring PyYAML when installed."""
    content = path.read_text(encoding="utf-8")
    if yaml is not None:
        try:
            raw = yaml.safe_load(content)
        except yaml.YAMLError as error:
            raise ValueError(f"manifest YAML 格式无效: {error}") from error
        if not isinstance(raw, dict):
            raise ValueError("manifest 根节点必须是对象")
        return raw

    root: dict[str, Any] = {}
    current_list: list[dict[str, Any]] | None = None
    current_list_key: str | None = None
    current_map: dict[str, Any] | None = None
    current_item: dict[str, Any] | None = None
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent, text = len(line) - len(line.lstrip()), line.strip()
        if indent == 0 and ":" in text:
            key, value = (part.strip() for part in text.split(":", 1))
            current_item = None
            if value:
                root[key] = _scalar(value)
                current_list = None
                current_list_key = None
                current_map = None
            else:
                root[key] = {}
                current_map = root[key]
                current_list = None
                current_list_key = key
        elif indent == 2 and text.startswith("- ") and current_list_key is not None:
            key, value = (part.strip() for part in text[2:].split(":", 1))
            if not isinstance(root.get(current_list_key), list):
                root[current_list_key] = []
            current_list = root[current_list_key]
            current_item = {key: _scalar(value)}
            current_list.append(current_item)
            current_map = None
        elif indent == 2 and ":" in text and current_map is not None:
            key, value = (part.strip() for part in text.split(":", 1))
            current_map[key] = _scalar(value)
        elif indent >= 4 and ":" in text and current_item is not None:
            key, value = (part.strip() for part in text.split(":", 1))
            current_item[key] = _scalar(value)
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
    category: str
    core_compatibility: str
    entry: str
    commands: list[Command]
    capabilities: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        raw = load_yaml_subset(path)
        required = ("schema_version", "name", "version", "description", "category", "core_compatibility", "entry", "commands", "capabilities")
        missing = [key for key in required if key not in raw]
        if missing: raise ValueError(f"manifest 缺少字段: {', '.join(missing)}")
        if raw["schema_version"] != "1" and raw["schema_version"] != 1: raise ValueError("仅支持 schema_version: 1")
        if not NAME_RE.fullmatch(str(raw["name"])): raise ValueError("插件 name 必须为小写字母、数字和连字符")
        if not VERSION_RE.fullmatch(str(raw["version"])): raise ValueError("插件 version 必须为语义化版本")
        if not isinstance(raw["category"], str) or not raw["category"].strip(): raise ValueError("插件 category 必须为非空字符串")
        if not isinstance(raw["core_compatibility"], str) or not raw["core_compatibility"].strip(): raise ValueError("插件 core_compatibility 必须为非空字符串")
        if not _core_compatible(raw["core_compatibility"]): raise ValueError("插件与当前 Core 版本不兼容")
        entry = str(raw["entry"])
        if not ENTRY_RE.fullmatch(entry): raise ValueError("entry 必须为 module:Class")
        module_name, _ = entry.split(":", 1)
        entry_path = path.parent / (module_name.replace(".", "/") + ".py")
        if not entry_path.is_file(): raise ValueError(f"插件入口不存在: {entry_path.relative_to(path.parent)}")
        if not isinstance(raw["commands"], list): raise ValueError("commands 必须为列表")
        commands = [Command(str(item.get("name", "")), str(item.get("description", "")), item.get("input_schema")) for item in raw["commands"] if isinstance(item, dict)]
        if not commands or any(not COMMAND_RE.fullmatch(command.name) for command in commands): raise ValueError("commands 必须包含小写点分命令")
        if len({command.name for command in commands}) != len(commands): raise ValueError("commands 不能包含重复命令")
        for command in commands:
            if command.input_schema:
                schema_path = path.parent / str(command.input_schema)
                try:
                    schema_path.relative_to(path.parent)
                except ValueError as error:
                    raise ValueError("input_schema 路径不能逃逸插件目录") from error
                if not schema_path.is_file(): raise ValueError(f"命令 Schema 不存在: {command.input_schema}")
        capabilities = raw["capabilities"]
        if not isinstance(capabilities, dict): raise ValueError("capabilities 必须为对象")
        if set(capabilities) - {"concurrency", "network", "filesystem", "resources"}: raise ValueError("capabilities 包含不支持的字段")
        if not isinstance(capabilities.get("concurrency"), bool) or not isinstance(capabilities.get("network"), bool): raise ValueError("capabilities.concurrency 和 network 必须为布尔值")
        if capabilities.get("filesystem") != "output-only": raise ValueError("capabilities.filesystem 必须为 output-only")
        if not isinstance(capabilities.get("resources"), list) or not all(isinstance(item, str) for item in capabilities["resources"]): raise ValueError("capabilities.resources 必须为字符串列表")
        return cls(path.parent, str(raw["name"]), str(raw["version"]), str(raw["description"]), str(raw["category"]), str(raw["core_compatibility"]), entry, commands, capabilities)
