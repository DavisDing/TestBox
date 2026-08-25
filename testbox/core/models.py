"""Stable Core task and execution models."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"


@dataclass(frozen=True)
class TaskPaths:
    root: Path
    input: Path
    output: Path
    logs: Path
    manifest: Path
    result: Path
    report: Path

    @classmethod
    def create(cls, root: Path) -> "TaskPaths":
        return cls(root, root / "input", root / "output", root / "logs", root / "manifest.json", root / "result.json", root / "report.md")
