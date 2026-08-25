"""Task workspace creation, file staging, output validation and export."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from testbox.core.models import TaskPaths


class WorkspaceManager:
    def __init__(self, root: Path, *, max_input_bytes: int, max_output_bytes: int):
        self.root = root
        self.max_input_bytes = max_input_bytes
        self.max_output_bytes = max_output_bytes

    def create(self, task_id: str) -> TaskPaths:
        paths = TaskPaths.create(self.root / task_id)
        for directory in (paths.input, paths.output, paths.logs):
            directory.mkdir(parents=True, exist_ok=True)
        return paths

    def stage_file_inputs(self, schema: dict[str, Any], params: dict[str, Any], paths: TaskPaths) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        staged_params = dict(params)
        records: list[dict[str, Any]] = []
        for key, definition in schema.get("properties", {}).items():
            if key not in params:
                continue
            single = definition.get("format") == "file-path"
            multiple = definition.get("type") == "array" and definition.get("items", {}).get("format") == "file-path"
            if not single and not multiple:
                continue
            values = [params[key]] if single else params[key]
            staged_values = []
            for index, value in enumerate(values):
                source = Path(value).expanduser().resolve()
                if not source.is_file():
                    raise ValueError(f"{key} 输入文件不存在: {source}")
                size = source.stat().st_size
                if size > self.max_input_bytes:
                    raise ValueError(f"{key} 输入文件超过 {self.max_input_bytes // (1024 * 1024)} MB 限制")
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                marker = f"-{index + 1}" if multiple else ""
                destination = paths.input / f"{key}{marker}-{digest[:12]}" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                staged_values.append(str(destination))
                records.append({"parameter": key, "source_path": str(source), "staged_path": str(destination.relative_to(paths.root)), "size": size, "sha256": digest})
            staged_params[key] = staged_values[0] if single else staged_values
        return staged_params, records

    def validate_outputs(self, paths: TaskPaths, files: list[str]) -> str | None:
        output_root = paths.output.resolve()
        total = 0
        for file_name in files:
            candidate = (paths.output / file_name).resolve()
            try:
                candidate.relative_to(output_root)
            except ValueError:
                return "INVALID_OUTPUT_PATH"
            if not candidate.is_file():
                return "MISSING_OUTPUT_FILE"
            total += candidate.stat().st_size
            if total > self.max_output_bytes:
                return "OUTPUT_TOO_LARGE"
        return None

    def export(self, task_workspace: Path, relative_path: str, destination: Path) -> Path:
        output_root = (task_workspace / "output").resolve()
        source = (output_root / relative_path).resolve()
        try:
            source.relative_to(output_root)
        except ValueError as error:
            raise ValueError("输出路径不合法") from error
        if not source.is_file():
            raise ValueError("输出文件不存在")
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.testbox.tmp")
        shutil.copy2(source, temporary)
        temporary.replace(destination)
        return destination
