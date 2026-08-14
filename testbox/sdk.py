"""The stable API made available to TestBox plugins."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass
class Result:
    status: Literal["success", "failed", "cancelled"]
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PluginError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.details = code, details or {}


class SafeFiles:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.output_dir / relative_path).resolve()
        if candidate != self.output_dir.resolve() and self.output_dir.resolve() not in candidate.parents:
            raise PluginError("INVALID_OUTPUT_PATH", "输出文件路径必须位于任务 output 目录内")
        return candidate

    def write_text(self, relative_path: str, content: str, encoding: str = "utf-8") -> str:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content, encoding=encoding)
        temporary.replace(target)
        return relative_path

    def write_bytes(self, relative_path: str, content: bytes) -> str:
        target = self.resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        return relative_path


@dataclass
class Workspace:
    root: Path
    input_dir: Path
    output_dir: Path


@dataclass
class Task:
    id: str


@dataclass
class Context:
    logger: Any
    config: dict[str, Any]
    workspace: Workspace
    files: SafeFiles
    task: Task
