"""Core Runtime facade shared by CLI and GUI."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from testbox.core.config import load_plugin_config
from testbox.core.errors import ErrorCode
from testbox.core.history import TaskHistory
from testbox.core.locks import PluginExecutionLock
from testbox.core.manifest import Manifest
from testbox.core.models import TaskPaths, TaskStatus
from testbox.core.plugin_packages import PluginPackageError, install_plugin as install_plugin_package, uninstall_plugin as uninstall_plugin_package
from testbox.core.plugin_registry import PluginManager
from testbox.core.process_runner import ProcessRunner
from testbox.core.report import write_json, write_report
from testbox.core.schema_validator import SchemaValidationError, SchemaValidator
from testbox.core.workspace import WorkspaceManager
from testbox.sdk import Result

class Runtime:
    MAX_INPUT_BYTES = 100 * 1024 * 1024
    MAX_OUTPUT_BYTES = 500 * 1024 * 1024

    def __init__(self, root: Path | None = None, *, timeout_seconds: float = 300.0):
        self.root = root or self._application_root()
        if root is not None or not getattr(sys, "frozen", False):
            self.plugins_dir = self.root / "plugins"
            self.workspace_dir = self.root / "workspace"
            bundled_plugins = self.plugins_dir
        else:
            data_dir = self._user_data_dir()
            self.plugins_dir = data_dir / "plugins"
            self.workspace_dir = data_dir / "workspace"
            bundled_plugins = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "plugins"
        self.bundled_plugins_dir = bundled_plugins
        plugin_dirs = [self.plugins_dir] if bundled_plugins == self.plugins_dir else [self.plugins_dir, bundled_plugins]
        self.manager = PluginManager(plugin_dirs)
        self.manager.discover()
        self.history = TaskHistory(self.workspace_dir / "task_history.sqlite3")
        self.history.abandon_incomplete(datetime.now(UTC).isoformat())
        self.timeout_seconds = timeout_seconds
        self.schema_validator = SchemaValidator()
        self.workspace = WorkspaceManager(self.workspace_dir, max_input_bytes=self.MAX_INPUT_BYTES, max_output_bytes=self.MAX_OUTPUT_BYTES)
        self.process_runner = ProcessRunner(self.root, timeout_seconds=timeout_seconds)

    def close(self) -> None:
        self.history.close()

    @staticmethod
    def _application_root() -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        current = Path.cwd()
        if (current / "plugins").is_dir():
            return current
        # Editable installs and source checkouts may be launched from an
        # arbitrary working directory. Prefer the project root that contains
        # the bundled plugins instead of silently exposing an empty registry.
        source_root = Path(__file__).resolve().parents[2]
        if (source_root / "plugins").is_dir():
            return source_root
        return current

    @staticmethod
    def _user_data_dir() -> Path:
        if sys.platform == "win32":
            return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "TestBox"
        return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "testbox"

    def _task_id(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4)

    @staticmethod
    def _redact_params(value: Any, key_context: str | None = None) -> Any:
        sensitive = ("password", "passwd", "secret", "token", "api_key", "apikey", "credential", "connection", "dsn")
        if key_context and any(marker in key_context.lower() for marker in sensitive):
            return "***"
        if isinstance(value, dict):
            return {key: Runtime._redact_params(item, str(key)) for key, item in value.items()}
        if isinstance(value, list):
            return [Runtime._redact_params(item, key_context) for item in value]
        if isinstance(value, tuple):
            return [Runtime._redact_params(item, key_context) for item in value]
        return value

    def list_plugins(self) -> list[Manifest]:
        unique: dict[str, Manifest] = {}
        for manifest in self.manager.available.values():
            unique[manifest.name] = manifest
        return sorted(unique.values(), key=lambda item: item.name)

    def list_commands(self) -> dict[str, Manifest]:
        return dict(self.manager.available)

    def reload_plugins(self) -> None:
        """重新扫描插件目录，使 GUI 安装/卸载后立即更新命令索引。"""
        self.manager.discover()

    def install_plugin(self, source: Path, *, force: bool = False) -> Manifest:
        """安装用户插件并刷新当前 Runtime 的插件索引。"""
        manifest = install_plugin_package(source, self.plugins_dir, force=force)
        self.reload_plugins()
        return manifest

    def uninstall_plugin(self, name: str) -> None:
        """卸载用户插件；冻结版不允许删除随程序发布的内置插件。"""
        target = self.plugins_dir / name
        if not target.is_dir():
            raise PluginPackageError(f"未安装插件: {name}")
        if self.bundled_plugins_dir.resolve() != self.plugins_dir.resolve():
            try:
                target.resolve().relative_to(self.bundled_plugins_dir.resolve())
            except ValueError:
                pass
            else:
                raise PluginPackageError("内置插件不能卸载，请先安装同名用户插件后再管理")
        uninstall_plugin_package(name, self.plugins_dir)
        self.reload_plugins()

    def get_command(self, command: str) -> Manifest:
        return self.manager.get_manifest(command)

    def inspect_plugin(self, identifier: str) -> dict[str, Any] | None:
        manifest = next((item for item in self.list_plugins() if item.name == identifier), None)
        if manifest is None and identifier in self.manager.available:
            manifest = self.manager.available[identifier]
        if manifest is None:
            return None
        return {
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "category": manifest.category,
            "core_compatibility": manifest.core_compatibility,
            "path": str(manifest.path),
            "entry": manifest.entry,
            "capabilities": manifest.capabilities,
            "commands": [
                {"name": item.name, "description": item.description, "input_schema": item.input_schema}
                for item in manifest.commands
            ],
        }

    def get_command_schema(self, command: str) -> dict[str, Any]:
        manifest = self.get_command(command)
        spec = next(item for item in manifest.commands if item.name == command)
        if not spec.input_schema:
            return {"type": "object", "properties": {}}
        return self.schema_validator.load(manifest.path / spec.input_schema)

    def _validate(self, manifest: Manifest, command: str, params: dict[str, Any]) -> dict[str, Any]:
        schema = self.get_command_schema(command)
        try:
            return self.schema_validator.validate(schema, params)
        except SchemaValidationError:
            raise

    def _stage_file_inputs(self, manifest: Manifest, command: str, params: dict[str, Any], input_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        paths = TaskPaths.create(input_dir.parent)
        paths = TaskPaths(paths.root, input_dir, paths.output, paths.logs, paths.manifest, paths.result, paths.report)
        return self.workspace.stage_file_inputs(self.get_command_schema(command), params, paths)

    def execute(self, command: str, params: dict[str, Any]) -> tuple[str, Result]:
        """Backward-compatible alias used by existing GUI callers."""
        return self.run(command, params)

    def run(self, command: str, params: dict[str, Any]) -> tuple[str, Result]:
        manifest = self.get_command(command)
        validated_params = self._validate(manifest, command, params)
        task_id = self._task_id()
        paths = self.workspace.create(task_id)
        try:
            staged_params, input_records = self.workspace.stage_file_inputs(self.get_command_schema(command), validated_params, paths)
        except Exception:
            # File staging happens before the task is inserted into history.
            # Do not leave an orphan workspace when an input is missing or
            # exceeds the configured limit.
            shutil.rmtree(paths.root, ignore_errors=True)
            raise
        started = datetime.now(UTC).isoformat()
        safe_params = self._redact_params(validated_params)
        write_json(paths.manifest, {"task_id": task_id, "plugin_name": manifest.name, "plugin_version": manifest.version, "command": command, "params": safe_params, "inputs": input_records, "started_at": started, "host_pid": None})
        self.history.create({"id": task_id, "plugin_name": manifest.name, "plugin_version": manifest.version, "command": command, "params": safe_params, "started_at": started, "result_path": str(paths.result), "workspace_path": str(paths.root), "host_pid": None})
        # The plugin may need credentials or other sensitive runtime settings
        # to execute. They are passed only to the Host process; persisted task
        # metadata contains redacted params and never contains this config.
        request = {"protocol_version": 1, "task_id": task_id, "plugin_path": str(manifest.path), "entry": manifest.entry, "command": command, "params": staged_params, "config": load_plugin_config(self.root, manifest.path, manifest.name), "workspace": str(paths.root), "capabilities": manifest.capabilities}
        execution_lock = None
        try:
            if not manifest.capabilities["concurrency"]:
                execution_lock = PluginExecutionLock(self.workspace_dir / ".locks" / f"{manifest.name}.lock")
                execution_lock.acquire()
            return self._execute_host(task_id, manifest, paths, request, execution_lock)
        except Exception as error:
            if execution_lock:
                execution_lock.release()
            result = Result("failed", "Core 执行任务时发生异常", data={"error_code": ErrorCode.CORE_EXECUTION_FAILED, "exception_type": type(error).__name__, "exception_message": str(error)})
            try:
                write_json(paths.result, result.to_dict())
                write_report(paths.report, task_id, manifest, result)
            finally:
                self.history.finish(task_id, status=TaskStatus.FAILED, finished_at=datetime.now(UTC).isoformat(), error_code=ErrorCode.CORE_EXECUTION_FAILED)
            return task_id, result

    @staticmethod
    def _result_from_payload(payload: dict[str, Any]) -> Result:
        if not isinstance(payload, dict):
            return Result("failed", "插件 Host 返回结果格式错误", data={"error_code": ErrorCode.HOST_RESULT_INVALID})
        status = payload.get("status")
        if status not in {"success", "failed", "cancelled"}:
            return Result("failed", "插件 Host 返回未知任务状态", data={"error_code": ErrorCode.HOST_RESULT_INVALID})
        message = payload.get("message")
        data = payload.get("data", {})
        files = payload.get("files", [])
        warnings = payload.get("warnings", [])
        if not isinstance(message, str) or not isinstance(data, dict) or not isinstance(files, list) or not all(isinstance(item, str) for item in files) or not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
            return Result("failed", "插件 Host 返回结果格式错误", data={"error_code": ErrorCode.HOST_RESULT_INVALID})
        return Result(status, message, data=data, files=files, warnings=warnings)

    def _execute_host(self, task_id: str, manifest: Manifest, paths: TaskPaths, request: dict[str, Any], execution_lock: PluginExecutionLock | None) -> tuple[str, Result]:
        def record_host_pid(host_pid: int) -> None:
            # Record the PID immediately after spawn so another Runtime
            # instance cannot mistake an actively starting task for a
            # crashed task during startup recovery.
            self.history.set_host_pid(task_id, host_pid)
            manifest_record = json.loads(paths.manifest.read_text(encoding="utf-8"))
            manifest_record["host_pid"] = host_pid
            write_json(paths.manifest, manifest_record)

        try:
            host_execution = self.process_runner.run(request, task_id=task_id, on_started=record_host_pid)
            result = self._result_from_payload(host_execution.payload)
            if result.status == "failed":
                diagnostics: dict[str, Any] = {"host_exit_code": host_execution.returncode}
                if host_execution.stderr.strip():
                    diagnostics["host_stderr"] = host_execution.stderr.strip()[-4_000:]
                task_log = paths.logs / "task.log"
                if task_log.is_file():
                    diagnostics["task_log_tail"] = task_log.read_text(encoding="utf-8", errors="replace")[-8_000:]
                result.data = {**result.data, **diagnostics}
            output_error = self.workspace.validate_outputs(paths, result.files)
            if output_error:
                messages = {ErrorCode.INVALID_OUTPUT_PATH: "插件返回了非法输出路径", ErrorCode.MISSING_OUTPUT_FILE: "插件声明的输出文件不存在", ErrorCode.OUTPUT_TOO_LARGE: "插件输出超过大小限制"}
                result = Result("failed", messages[output_error], data={"error_code": output_error})
            write_json(paths.result, result.to_dict())
            write_report(paths.report, task_id, manifest, result)
            status = {"success": TaskStatus.SUCCEEDED, "failed": TaskStatus.FAILED, "cancelled": TaskStatus.CANCELLED}[result.status]
            self.history.finish(task_id, status=status, finished_at=datetime.now(UTC).isoformat(), error_code=result.data.get("error_code"))
            return task_id, result
        finally:
            if execution_lock:
                execution_lock.release()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.history.get(task_id)

    def get_task_result(self, task_id: str) -> dict[str, Any] | None:
        record = self.get_task(task_id)
        if not record:
            return None
        path = Path(record["result_path"])
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_tasks(self, *, status: str | TaskStatus | None = None, command: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.history.list_tasks(status=status, command=command, limit=limit, offset=offset)

    def count_tasks(self, *, status: str | TaskStatus | None = None, command: str | None = None) -> int:
        return self.history.count(status=status, command=command)

    def clean_workspace(self, before: date) -> int:
        removed = 0
        if not self.workspace_dir.exists():
            return removed
        for task_dir in self.workspace_dir.iterdir():
            if not task_dir.is_dir():
                continue
            try:
                started = datetime.strptime(task_dir.name.split("-", 1)[0], "%Y%m%dT%H%M%S").date()
            except ValueError:
                continue
            if started < before:
                shutil.rmtree(task_dir)
                removed += 1
        return removed

    def commit_output(self, task_id: str, relative_path: str, destination: Path) -> Path:
        record = self.get_task(task_id)
        if not record:
            raise LookupError("未找到任务")
        result = self.get_task_result(task_id)
        if not result or result.get("status") != "success" or relative_path not in result.get("files", []):
            raise ValueError("只能提交成功任务声明的输出文件")
        return self.workspace.export(Path(record["workspace_path"]), relative_path, destination)
