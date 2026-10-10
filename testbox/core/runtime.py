"""Core Runtime facade shared by CLI and GUI."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
from datetime import UTC, date, datetime, time as datetime_time, tzinfo
from pathlib import Path
from typing import Any

from testbox.core.config import load_plugin_config
from testbox.core.errors import ErrorCode
from testbox.core.history import TaskHistory
from testbox.core.locks import PluginExecutionLock
from testbox.core.manifest import Manifest
from testbox.core.models import TaskPaths, TaskStatus
from testbox import __version__
from testbox.core.plugin_packages import PluginPackageError, inspect_plugin as inspect_plugin_package, install_plugin as install_plugin_package, package_plugin as package_plugin_archive, uninstall_plugin as uninstall_plugin_package
from testbox.core.plugin_registry import PluginManager
from testbox.core.process_runner import ProcessRunner
from testbox.core.report import write_json, write_report
from testbox.core.redaction import Redactor
from testbox.core.schema_validator import SchemaValidationError, SchemaValidator
from testbox.core.workspace import WorkspaceManager
from testbox.sdk import Result

class Runtime:
    MAX_INPUT_BYTES = 100 * 1024 * 1024
    MAX_OUTPUT_BYTES = 500 * 1024 * 1024

    def __init__(self, root: Path | None = None, *, timeout_seconds: float = 300.0):
        self.root = root or self._application_root()
        packaged_plugins = Path(__file__).resolve().parents[1] / "_bundled_plugins"
        if root is not None:
            # Explicit roots remain hermetic for tests and embedding.
            self.plugins_dir = self.root / "plugins"
            self.workspace_dir = self.root / "workspace"
            bundled_plugins = self.plugins_dir
        elif getattr(sys, "frozen", False):
            data_dir = self._user_data_dir()
            self.plugins_dir = data_dir / "plugins"
            self.workspace_dir = data_dir / "workspace"
            bundled_plugins = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "plugins"
        elif packaged_plugins.is_dir():
            # Wheels include read-only official resources inside the package;
            # tasks and installed extensions never write to site-packages/cwd.
            data_dir = self._user_data_dir()
            self.plugins_dir = data_dir / "plugins"
            self.workspace_dir = data_dir / "workspace"
            bundled_plugins = packaged_plugins
        else:
            self.plugins_dir = self.root / "plugins"
            self.workspace_dir = self.root / "workspace"
            bundled_plugins = self.plugins_dir
        self.bundled_plugins_dir = bundled_plugins
        plugin_dirs = [self.plugins_dir] if bundled_plugins == self.plugins_dir else [self.plugins_dir, bundled_plugins]
        self.manager = PluginManager(plugin_dirs)
        self.manager.discover()
        self.history = TaskHistory(self.workspace_dir / "task_history.sqlite3")
        self.history.abandon_incomplete(
            datetime.now(UTC).isoformat(), is_task_active=self._task_is_active,
        )
        # Infrastructure exists before task snapshots; failed input staging must
        # not appear to have left a newly created task directory behind.
        (self.workspace_dir / ".locks").mkdir(exist_ok=True)
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
        package_root = Path(__file__).resolve().parents[1]
        if (package_root / "_bundled_plugins").is_dir():
            return package_root
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
        """Compatibility helper; all persisted diagnostics use the same redactor."""
        if key_context is not None:
            return Redactor({key_context: value}).value({key_context: value})[key_context]
        return Redactor(value).value(value)

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

    def list_unavailable_plugins(self) -> list[dict[str, str]]:
        """返回插件发现阶段不可用项，供 CLI/GUI 展示诊断原因。"""
        return [
            {"path": path, "reason": reason, "status": "unavailable"}
            for path, reason in sorted(self.manager.unavailable.items())
        ]

    def validate_plugin(self, source: Path) -> Manifest:
        """Validate a plugin directory or ZIP through the Runtime facade."""
        return inspect_plugin_package(source)

    def _validate_plugin_install_conflicts(self, manifest: Manifest) -> None:
        conflicts = sorted(
            command.name
            for command in manifest.commands
            if command.name in self.manager.available
            and self.manager.available[command.name].name != manifest.name
        )
        if conflicts:
            owners = sorted({self.manager.available[command].name for command in conflicts})
            raise PluginPackageError(
                f"插件命令冲突: {', '.join(conflicts)}（已有插件: {', '.join(owners)}）"
            )

    def preview_plugin_install(self, source: Path) -> dict[str, Any]:
        """Return validated package metadata without changing the plugin directory."""
        manifest = inspect_plugin_package(source)
        self._validate_plugin_install_conflicts(manifest)
        return {
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "category": manifest.category,
            "core_compatibility": manifest.core_compatibility,
            "commands": [item.name for item in manifest.commands],
        }

    def package_plugin(self, source: Path, destination: Path) -> Path:
        """Package a plugin through the Runtime facade."""
        return package_plugin_archive(source, destination)

    def install_plugin(self, source: Path, *, force: bool = False) -> Manifest:
        """安装用户插件并刷新当前 Runtime 的插件索引。"""
        management_lock = PluginExecutionLock(self.workspace_dir / ".locks" / "plugin-management.lock")
        management_lock.acquire()
        try:
            self.reload_plugins()
            manifest = install_plugin_package(
                source,
                self.plugins_dir,
                force=force,
                validate=self._validate_plugin_install_conflicts,
            )
            self.reload_plugins()
            return manifest
        finally:
            management_lock.release()

    def uninstall_plugin(self, name: str) -> None:
        """卸载用户插件；冻结版不允许删除随程序发布的内置插件。"""
        management_lock = PluginExecutionLock(self.workspace_dir / ".locks" / "plugin-management.lock")
        management_lock.acquire()
        try:
            self.reload_plugins()
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
        finally:
            management_lock.release()

    def get_command(self, command: str) -> Manifest:
        return self.manager.get_manifest(command)

    def can_uninstall_plugin(self, name: str) -> bool:
        """Return whether the named plugin is installed in the writable user plugin directory."""
        target = self.plugins_dir / name
        if not target.is_dir():
            return False
        if self.bundled_plugins_dir.resolve() == self.plugins_dir.resolve():
            return True
        try:
            target.resolve().relative_to(self.plugins_dir.resolve())
        except ValueError:
            return False
        return True

    def get_runtime_diagnostics(self) -> dict[str, str]:
        """Expose stable read-only Runtime paths and execution protocol details to clients."""
        return {
            "version": __version__,
            "runtime_root": str(self.root),
            "workspace_dir": str(self.workspace_dir),
            "plugins_dir": str(self.plugins_dir),
            "bundled_plugins_dir": str(self.bundled_plugins_dir),
            "history_path": str(self.history.path),
            "host_protocol": "single-request/single-response",
            "plugin_host_boundary": "process isolation for failures; not a malicious-code security sandbox",
        }

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
            "uninstallable": self.can_uninstall_plugin(manifest.name),
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

    def validate_params(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        """Validate command parameters without creating a task or staging files."""
        return self.schema_validator.validate(self.get_command_schema(command), params)

    def _validate(self, manifest: Manifest, command: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.validate_params(command, params)

    def _stage_file_inputs(self, manifest: Manifest, command: str, params: dict[str, Any], input_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        paths = TaskPaths.create(input_dir.parent)
        paths = TaskPaths(paths.root, input_dir, paths.output, paths.logs, paths.manifest, paths.result, paths.report)
        return self.workspace.stage_file_inputs(self.get_command_schema(command), params, paths)

    def execute(self, command: str, params: dict[str, Any]) -> tuple[str, Result]:
        """Backward-compatible alias used by existing GUI callers."""
        return self.run(command, params)

    def _task_lock(self, task_id: str) -> PluginExecutionLock:
        return PluginExecutionLock(self.workspace_dir / ".locks" / f"task-{task_id}.lock")

    def _task_is_active(self, task_id: str) -> bool:
        lock = self._task_lock(task_id)
        if not lock.try_acquire():
            return True
        lock.release()
        return False

    def run(self, command: str, params: dict[str, Any]) -> tuple[str, Result]:
        manifest = self.get_command(command)
        validated_params = self._validate(manifest, command, params)
        # Fail invalid configuration before creating an incomplete task. Original
        # settings remain in memory only and are redacted before persistence.
        config = load_plugin_config(self.root, manifest.path, manifest.name)
        redactor = Redactor(validated_params, config)
        task_id = self._task_id()
        task_lock = self._task_lock(task_id)
        task_lock.acquire()
        execution_lock = None
        paths = None
        recorded = False
        try:
            paths = self.workspace.create(task_id)
            try:
                staged_params, input_records = self.workspace.stage_file_inputs(
                    self.get_command_schema(command), validated_params, paths
                )
            except Exception:
                shutil.rmtree(paths.root, ignore_errors=True)
                raise
            started = datetime.now(UTC).isoformat()
            safe_params = redactor.value(validated_params)
            write_json(paths.manifest, {
                "task_id": task_id, "plugin_name": manifest.name,
                "plugin_version": manifest.version, "command": command,
                "params": safe_params, "inputs": redactor.value(input_records),
                "started_at": started, "host_pid": None, "owner_pid": os.getpid(),
            })
            self.history.create({
                "id": task_id, "plugin_name": manifest.name,
                "plugin_version": manifest.version, "command": command,
                "params": safe_params, "started_at": started,
                "result_path": str(paths.result), "workspace_path": str(paths.root),
                "host_pid": None, "owner_pid": os.getpid(),
            })
            recorded = True
            request = {
                "protocol_version": 1, "task_id": task_id,
                "plugin_path": str(manifest.path), "entry": manifest.entry,
                "command": command, "params": staged_params, "config": config,
                "workspace": str(paths.root), "capabilities": manifest.capabilities,
            }
            try:
                if not manifest.capabilities["concurrency"]:
                    execution_lock = PluginExecutionLock(
                        self.workspace_dir / ".locks" / f"{manifest.name}.lock"
                    )
                    execution_lock.acquire()
                return self._execute_host(task_id, manifest, paths, request, redactor)
            except Exception as error:
                result = Result("failed", "Core 执行任务时发生异常", data={
                    "error_code": ErrorCode.CORE_EXECUTION_FAILED,
                    "exception_type": type(error).__name__,
                    "exception_message": redactor.text(str(error)),
                })
                self._persist_result(task_id, manifest, paths, result, redactor)
                return task_id, result
        except Exception:
            # Failures before the history insert are not runnable tasks. Preserve
            # existing user data but remove only this newly created workspace.
            if not recorded and paths is not None:
                shutil.rmtree(paths.root, ignore_errors=True)
            raise
        finally:
            if execution_lock is not None:
                execution_lock.release()
            task_lock.release()

    def _persist_result(
        self, task_id: str, manifest: Manifest, paths: TaskPaths,
        result: Result, redactor: Redactor,
    ) -> None:
        """History bookkeeping must never replace the plugin's business result."""
        safe = redactor.value(result.to_dict())
        result.message, result.data = safe["message"], safe["data"]
        result.warnings = safe["warnings"]
        write_json(paths.result, result.to_dict())
        write_report(paths.report, task_id, manifest, result)
        status = {
            "success": TaskStatus.SUCCEEDED, "failed": TaskStatus.FAILED,
            "cancelled": TaskStatus.CANCELLED,
        }[result.status]
        finished_at = datetime.now(UTC).isoformat()
        history_warning = "任务结果已保存，但任务历史同步失败；可通过任务工作区查看原始结果。"
        had_history_error = False
        synchronized = False
        for _ in range(2):
            try:
                self.history.finish(
                    task_id, status=status, finished_at=finished_at,
                    error_code=result.data.get("error_code"),
                )
                synchronized = True
                break
            except Exception:
                had_history_error = True
                # TaskHistory owns transaction rollback. A bounded retry must
                # not change the plugin's status or erase its output list.
        if had_history_error:
            result.warnings.append(
                "任务历史同步曾失败，重试后已恢复；原始任务结果和产物已保留。"
                if synchronized else history_warning
            )
            # A warning-write failure cannot erase the already persisted result.
            try:
                write_json(paths.result, result.to_dict())
                write_report(paths.report, task_id, manifest, result)
            except OSError:
                pass

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

    def _execute_host(self, task_id: str, manifest: Manifest, paths: TaskPaths, request: dict[str, Any], redactor: Redactor) -> tuple[str, Result]:
        def record_host_pid(host_pid: int) -> None:
            # Record the PID immediately after spawn so another Runtime
            # instance cannot mistake an actively starting task for a
            # crashed task during startup recovery.
            self.history.set_host_pid(task_id, host_pid)
            manifest_record = json.loads(paths.manifest.read_text(encoding="utf-8"))
            manifest_record["host_pid"] = host_pid
            write_json(paths.manifest, manifest_record)

        host_execution = self.process_runner.run(request, task_id=task_id, on_started=record_host_pid)
        result = self._result_from_payload(host_execution.payload)
        if result.status == "failed":
            diagnostics: dict[str, Any] = {"host_exit_code": host_execution.returncode}
            if host_execution.stderr.strip():
                diagnostics["host_stderr"] = redactor.text(host_execution.stderr.strip())[-4_000:]
            task_log = paths.logs / "task.log"
            if task_log.is_file():
                diagnostics["task_log_tail"] = redactor.text(self._read_log_tail(task_log, 8_000))
            result.data = {**result.data, **diagnostics}
        output_error = self.workspace.validate_outputs(paths, result.files)
        if output_error:
            messages = {
                ErrorCode.INVALID_OUTPUT_PATH: "插件返回了非法输出路径",
                ErrorCode.MISSING_OUTPUT_FILE: "插件声明的输出文件不存在",
                ErrorCode.OUTPUT_TOO_LARGE: "插件输出超过大小限制",
            }
            result = Result("failed", messages[output_error], data={"error_code": output_error})
        self._persist_result(task_id, manifest, paths, result, redactor)
        return task_id, result

    @staticmethod
    def _read_log_tail(path: Path, max_chars: int) -> str:
        # At most four UTF-8 bytes per character plus a partial boundary. Do not
        # load the entire log merely to display a short diagnostic tail.
        if max_chars <= 0:
            return ""
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - max_chars * 4))
            text = stream.read(max_chars * 4).decode("utf-8", errors="replace")
            # Match text-mode universal newlines before taking a character
            # tail; counting CRLF twice chops the first visible character.
            return text.replace("\r\n", "\n").replace("\r", "\n")[-max_chars:]

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

    def get_task_report(self, task_id: str) -> str | None:
        """Return the Runtime-generated Markdown report for a task."""
        record = self.get_task(task_id)
        if not record:
            return None
        path = Path(record["workspace_path"]) / "report.md"
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def get_task_log(self, task_id: str, *, max_chars: int = 8_000) -> str | None:
        """Return the tail of the task log without exposing workspace I/O to GUI callers."""
        record = self.get_task(task_id)
        if not record:
            return None
        path = Path(record["workspace_path"]) / "logs" / "task.log"
        if not path.is_file():
            return None
        try:
            return self._read_log_tail(path, max_chars)
        except OSError:
            return None

    def list_tasks(
        self,
        *,
        status: str | TaskStatus | None = None,
        command: str | None = None,
        task_id_query: str | None = None,
        started_from: str | None = None,
        started_before: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self.history.list_tasks(
            status=status,
            command=command,
            task_id_query=task_id_query,
            started_from=started_from,
            started_before=started_before,
            limit=limit,
            offset=offset,
        )

    def count_tasks(
        self,
        *,
        status: str | TaskStatus | None = None,
        command: str | None = None,
        task_id_query: str | None = None,
        started_from: str | None = None,
        started_before: str | None = None,
    ) -> int:
        return self.history.count(
            status=status,
            command=command,
            task_id_query=task_id_query,
            started_from=started_from,
            started_before=started_before,
        )

    @staticmethod
    def _local_midnight_utc(before: date, local_timezone: tzinfo | None = None) -> datetime:
        """Convert a user-facing local date boundary to an exact UTC instant."""
        if local_timezone is None:
            local_midnight = datetime.combine(before, datetime_time.min).astimezone()
        else:
            local_midnight = datetime.combine(before, datetime_time.min, tzinfo=local_timezone)
        return local_midnight.astimezone(UTC)

    def clean_workspace(self, before: date) -> int:
        """Remove old inactive workspaces without racing task creation/execution."""
        removed = 0
        if not self.workspace_dir.exists():
            return removed
        cutoff = self._local_midnight_utc(before)
        for task_dir in self.workspace_dir.iterdir():
            if task_dir.is_symlink() or not task_dir.is_dir():
                continue
            try:
                started = datetime.strptime(
                    task_dir.name.split("-", 1)[0], "%Y%m%dT%H%M%S"
                ).replace(tzinfo=UTC)
            except ValueError:
                continue
            if started >= cutoff:
                continue
            task_lock = self._task_lock(task_dir.name)
            if not task_lock.try_acquire():
                continue
            try:
                record = self.get_task(task_dir.name)
                if record and record["status"] not in {
                    "SUCCEEDED", "FAILED", "CANCELLED", "ABANDONED",
                }:
                    continue
                if task_dir.exists():
                    shutil.rmtree(task_dir)
                    removed += 1
            finally:
                task_lock.release()
        return removed

    def clean_history(self, before: date) -> int:
        """Remove terminal history before the selected date; keep active tasks."""
        cutoff = self._local_midnight_utc(before)
        return self.history.clean_before(cutoff.isoformat())

    def get_task_output_path(self, task_id: str, relative_path: str) -> Path:
        """Resolve one declared successful output for a downstream Runtime task.

        The facade deliberately validates task status and the declared output list
        before returning a path.  GUI callers can therefore prefill a downstream
        form without reading task workspaces or constructing workspace paths.
        """
        record = self.get_task(task_id)
        if not record:
            raise LookupError("未找到任务")
        result = self.get_task_result(task_id)
        if not result or result.get("status") != "success" or relative_path not in result.get("files", []):
            raise ValueError("只能使用成功任务声明的输出文件")
        return self.workspace.resolve_output(Path(record["workspace_path"]), relative_path)

    def get_task_artifact_path(self, task_id: str, relative_path: str) -> Path:
        """Inspect declared reports from terminal tasks, including partial batch failures.

        Does not authorize reuse as a successful downstream input.
        """
        record = self.get_task(task_id)
        result = self.get_task_result(task_id) if record else None
        if not record or not result or result.get("status") not in {"success", "failed"} or relative_path not in result.get("files", []):
            raise ValueError("只能读取已结束任务声明的报告")
        return self.workspace.resolve_output(Path(record["workspace_path"]), relative_path)

    def commit_output(self, task_id: str, relative_path: str, destination: Path) -> Path:
        self.get_task_output_path(task_id, relative_path)
        record = self.get_task(task_id)
        if not record:
            raise LookupError("未找到任务")
        return self.workspace.export(Path(record["workspace_path"]), relative_path, destination)

    def commit_outputs_archive(self, task_id: str, destination: Path) -> Path:
        record = self.get_task(task_id)
        if not record:
            raise LookupError("未找到任务")
        result = self.get_task_result(task_id)
        if not result or result.get("status") != "success":
            raise ValueError("只能提交成功任务声明的输出文件")
        files = result.get("files", [])
        if not files:
            raise ValueError("该任务没有可导出的产物文件")
        return self.workspace.export_archive(Path(record["workspace_path"]), files, destination)
