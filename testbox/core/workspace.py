"""Task workspace creation, file staging, output validation and export."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any

from testbox.core.models import TaskPaths


INPUT_CHUNK_BYTES = 1024 * 1024


def _relative_output_path(value: str) -> Path:
    # Check syntax before resolve(): absolute paths or '..' are invalid even
    # when their normalized destination happens to remain inside output/.
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("输出路径不合法")
    normalized = value.replace("\\", "/")
    windows_path = PureWindowsPath(normalized)
    if (
        normalized.startswith("/") or windows_path.drive
        or ".." in normalized.split("/")
        or any(":" in part for part in normalized.split("/"))
    ):
        raise ValueError("输出路径不合法")
    return Path(normalized)


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
        total = 0
        # Opt-in source policies must run before resolve()/copy: the Host sees
        # regular snapshots and cannot recover whether the user selected a link
        # or the same original file twice. Existing schemas remain unchanged.
        seen_sources: set[Path] = set()
        for key, definition in schema.get("properties", {}).items():
            policy = definition.get("x-input-policy")
            if key not in params or not isinstance(policy, dict):
                continue
            single = definition.get("format") == "file-path"
            multiple = definition.get("type") == "array" and definition.get("items", {}).get("format") == "file-path"
            if not single and not multiple:
                continue
            values = [params[key]] if single else params[key]
            maximum = policy.get("max_files")
            if isinstance(maximum, int) and len(values) > maximum:
                raise ValueError(f"{key} 最多支持 {maximum} 个文件")
            for value in values:
                selected = Path(value).expanduser()
                if policy.get("reject_symlinks") is True and selected.is_symlink():
                    raise ValueError(f"{key} 不接受符号链接输入")
                resolved = selected.resolve()
                if policy.get("unique_sources") is True:
                    if resolved in seen_sources:
                        raise ValueError(f"{key} 存在重复来源文件")
                    seen_sources.add(resolved)
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
                if source.stat().st_size > self.max_input_bytes - total:
                    raise ValueError(f"{key} 任务累计输入文件超过 {self.max_input_bytes} 字节限制")
                # Copy and hash the same stream. This bounds memory and ensures
                # sha256 describes the staged bytes even if the source changes.
                hasher = hashlib.sha256()
                size = 0
                temporary: Path | None = None
                try:
                    with source.open("rb") as input_file, tempfile.NamedTemporaryFile(
                        dir=paths.input, prefix=".staging-", delete=False,
                    ) as output_file:
                        temporary = Path(output_file.name)
                        while True:
                            remaining = self.max_input_bytes - total - size
                            chunk = input_file.read(min(INPUT_CHUNK_BYTES, remaining + 1))
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > self.max_input_bytes - total:
                                raise ValueError(f"{key} 任务累计输入文件超过 {self.max_input_bytes} 字节限制")
                            hasher.update(chunk)
                            output_file.write(chunk)
                    digest = hasher.hexdigest()
                    marker = f"-{index + 1}" if multiple else ""
                    destination = paths.input / f"{key}{marker}-{digest[:12]}" / source.name
                    destination.resolve().relative_to(paths.input.resolve())
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copystat(source, temporary)
                    temporary.replace(destination)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                total += size
                staged_values.append(str(destination))
                records.append({"parameter": key, "source_path": str(source), "staged_path": str(destination.relative_to(paths.root)), "size": size, "sha256": digest})
            staged_params[key] = staged_values[0] if single else staged_values
        return staged_params, records

    def validate_outputs(self, paths: TaskPaths, files: list[str]) -> str | None:
        output_root = paths.output.resolve()
        total = 0
        for file_name in files:
            try:
                candidate = (output_root / _relative_output_path(file_name)).resolve()
                candidate.relative_to(output_root)
            except ValueError:
                return "INVALID_OUTPUT_PATH"
            if not candidate.is_file():
                return "MISSING_OUTPUT_FILE"
            total += candidate.stat().st_size
            if total > self.max_output_bytes:
                return "OUTPUT_TOO_LARGE"
        return None

    def resolve_output(self, task_workspace: Path, relative_path: str) -> Path:
        """Return one existing output after enforcing the task-output boundary."""
        output_root = (task_workspace / "output").resolve()
        try:
            source = (output_root / _relative_output_path(relative_path)).resolve()
            source.relative_to(output_root)
        except ValueError as error:
            raise ValueError("输出路径不合法") from error
        if not source.is_file():
            raise ValueError("输出文件不存在")
        return source

    def export(self, task_workspace: Path, relative_path: str, destination: Path) -> Path:
        source = self.resolve_output(task_workspace, relative_path)
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.testbox.tmp")
        shutil.copy2(source, temporary)
        temporary.replace(destination)
        return destination

    def export_archive(self, task_workspace: Path, relative_paths: list[str], destination: Path) -> Path:
        if not relative_paths:
            raise ValueError("没有可导出的产物文件")
        sources = [(rel, self.resolve_output(task_workspace, rel)) for rel in relative_paths]
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.testbox.tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for rel, source in sources:
                # 统一为正斜杠相对路径写入压缩包，保持目录层级结构一致
                archive_name = rel.replace(chr(92), "/")
                archive.write(source, arcname=archive_name)
        temporary.replace(destination)
        return destination
