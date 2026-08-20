from __future__ import annotations

import hashlib, json, os, secrets, shutil, sqlite3, subprocess, sys, threading, time
from datetime import UTC, datetime, date
from pathlib import Path
from typing import Any

from testbox.core.manifest import Manifest
from testbox.core.config import load_plugin_config
from testbox.core.history import TaskHistory
from testbox.sdk import Result

EXIT_CODES = {"success": 0, "cancelled": 130, "failed": 4}


_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[Path, threading.Lock] = {}


def _process_lock(path: Path) -> threading.Lock:
    """Return the process-local companion lock for a cross-process lock file."""
    resolved = path.resolve()
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(resolved, threading.Lock())


class PluginExecutionLock:
    """A cross-process lock used for plugins that declare no concurrency support."""
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.local_lock = _process_lock(self.path)
        self.handle = None

    def __del__(self):
        self.release()

    def acquire(self) -> None:
        self.local_lock.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+b")
            self.handle.seek(0)
            self.handle.write(b"0")
            self.handle.flush()
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                # LK_LOCK gives up after roughly ten seconds. Retry the
                # non-blocking operation so a long-running plugin remains
                # serialized instead of failing spuriously on Windows.
                while True:
                    try:
                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        except Exception:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            self.local_lock.release()
            raise

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
            self.local_lock.release()


class PluginManager:
    def __init__(self, plugins_dirs: list[Path]):
        self.plugins_dirs = plugins_dirs; self.available: dict[str, Manifest] = {}; self.unavailable: dict[str, str] = {}

    def discover(self) -> None:
        self.available.clear(); self.unavailable.clear(); seen: set[str] = set()
        for plugins_dir in self.plugins_dirs:
            if not plugins_dir.exists(): continue
            for manifest_path in sorted(plugins_dir.glob("*/manifest.yaml")):
                try:
                    manifest = Manifest.load(manifest_path)
                    for command in manifest.commands:
                        if command.name in seen:
                            if self.available[command.name].name == manifest.name: continue
                            raise ValueError(f"命令重复: {command.name}")
                        seen.add(command.name); self.available[command.name] = manifest
                except Exception as error:
                    self.unavailable[str(manifest_path.parent)] = str(error)


class Runtime:
    MAX_INPUT_BYTES = 100 * 1024 * 1024
    MAX_OUTPUT_BYTES = 500 * 1024 * 1024
    def __init__(self, root: Path | None = None, *, timeout_seconds: float = 300.0):
        self.root = root or self._application_root()
        if root is not None or not getattr(sys, "frozen", False):
            self.plugins_dir = self.root / "plugins"; self.workspace_dir = self.root / "workspace"; bundled_plugins = self.plugins_dir
        else:
            data_dir = self._user_data_dir(); self.plugins_dir = data_dir / "plugins"; self.workspace_dir = data_dir / "workspace"
            bundled_plugins = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "plugins"
        # User-installed plugins take precedence over bundled copies, enabling upgrades
        # without writing to a protected Windows installation directory.
        self.manager = PluginManager([self.plugins_dir] if bundled_plugins == self.plugins_dir else [self.plugins_dir, bundled_plugins]); self.manager.discover()
        self.history = TaskHistory(self.workspace_dir / "task_history.sqlite3")
        self.history.abandon_incomplete(datetime.now(UTC).isoformat())
        self.timeout_seconds = timeout_seconds

    def close(self) -> None:
        self.history.close()

    @staticmethod
    def _application_root() -> Path:
        if getattr(sys, "frozen", False): return Path(sys.executable).resolve().parent
        return Path.cwd()

    @staticmethod
    def _user_data_dir() -> Path:
        if sys.platform == "win32": return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "TestBox"
        return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "testbox"

    def _task_id(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4)

    @staticmethod
    def _redact_params(params: dict[str, Any]) -> dict[str, Any]:
        sensitive = ("password", "passwd", "secret", "token", "api_key", "apikey", "credential")
        return {key: "***" if any(marker in key.lower() for marker in sensitive) else value for key, value in params.items()}

    def _validate(self, manifest: Manifest, command: str, params: dict[str, Any]) -> None:
        spec = next(item for item in manifest.commands if item.name == command)
        if not spec.input_schema: return
        schema_path = manifest.path / spec.input_schema
        if not schema_path.exists(): raise ValueError("命令 Schema 不存在")
        schema = json.loads(schema_path.read_text(encoding="utf-8")); properties = schema.get("properties", {})
        unknown = set(params) - set(properties)
        if unknown: raise ValueError(f"未知参数: {', '.join(sorted(unknown))}")
        for key in schema.get("required", []):
            if key not in params: raise ValueError(f"缺少必填参数: {key}")
        for key, value in params.items():
            kind = properties[key].get("type")
            if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)): raise ValueError(f"{key} 必须为整数")
            if kind == "string" and not isinstance(value, str): raise ValueError(f"{key} 必须为字符串")
            if kind == "boolean" and not isinstance(value, bool): raise ValueError(f"{key} 必须为布尔值")
            if kind == "object" and not isinstance(value, dict): raise ValueError(f"{key} 必须为对象")
            if kind == "array" and not isinstance(value, list): raise ValueError(f"{key} 必须为数组")
            if "enum" in properties[key] and value not in properties[key]["enum"]: raise ValueError(f"{key} 取值不受支持")
            if "minimum" in properties[key] and value < properties[key]["minimum"]: raise ValueError(f"{key} 不能小于 {properties[key]['minimum']}")
            if "maximum" in properties[key] and value > properties[key]["maximum"]: raise ValueError(f"{key} 不能大于 {properties[key]['maximum']}")

    def _stage_file_inputs(self, manifest: Manifest, command: str, params: dict[str, Any], input_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Copy schema-declared file inputs into the task boundary before starting Host."""
        spec = next(item for item in manifest.commands if item.name == command)
        if not spec.input_schema:
            return params, []
        schema = json.loads((manifest.path / spec.input_schema).read_text(encoding="utf-8"))
        staged_params = params.copy()
        records: list[dict[str, Any]] = []
        for key, definition in schema.get("properties", {}).items():
            if key not in params:
                continue
            is_single = definition.get("format") == "file-path"
            is_array = definition.get("type") == "array" and definition.get("items", {}).get("format") == "file-path"
            if not is_single and not is_array:
                continue
            values = [params[key]] if is_single else params[key]
            staged_values = []
            for index, value in enumerate(values):
                source = Path(value).expanduser().resolve()
                if not source.is_file():
                    raise ValueError(f"{key} 输入文件不存在: {source}")
                size = source.stat().st_size
                if size > self.MAX_INPUT_BYTES:
                    raise ValueError(f"{key} 输入文件超过 {self.MAX_INPUT_BYTES // (1024 * 1024)} MB 限制")
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                marker = f"-{index + 1}" if is_array else ""
                destination = input_dir / f"{key}{marker}-{digest[:12]}" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                staged_values.append(str(destination))
                records.append({"parameter": key, "source_path": str(source), "staged_path": str(destination.relative_to(input_dir.parent)), "size": size, "sha256": digest})
            staged_params[key] = staged_values[0] if is_single else staged_values
        return staged_params, records

    def run(self, command: str, params: dict[str, Any]) -> tuple[str, Result]:
        if command not in self.manager.available: raise LookupError(f"未找到可用命令: {command}")
        manifest = self.manager.available[command]; self._validate(manifest, command, params)
        task_id = self._task_id(); task_dir = self.workspace_dir / task_id
        for child in ("input", "output", "logs"): (task_dir / child).mkdir(parents=True, exist_ok=True)
        staged_params, input_records = self._stage_file_inputs(manifest, command, params, task_dir / "input")
        started = datetime.now(UTC).isoformat(); safe_params = self._redact_params(params)
        self._write_json(task_dir / "manifest.json", {"task_id": task_id, "plugin_name": manifest.name, "plugin_version": manifest.version, "command": command, "params": safe_params, "inputs": input_records, "started_at": started, "host_pid": None})
        self.history.create({"id": task_id, "plugin_name": manifest.name, "plugin_version": manifest.version, "command": command, "params": safe_params, "started_at": started, "result_path": str(task_dir / "result.json"), "workspace_path": str(task_dir), "host_pid": None})
        plugin_config = load_plugin_config(self.root, manifest.path, manifest.name)
        request = {"protocol_version": 1, "task_id": task_id, "plugin_path": str(manifest.path), "entry": manifest.entry, "command": command, "params": staged_params, "config": self._redact_params(plugin_config), "workspace": str(task_dir), "capabilities": manifest.capabilities}
        environment = os.environ.copy()
        if getattr(sys, "frozen", False): host_command = [sys.executable, "--plugin-host"]
        else:
            package_root = str(Path(__file__).resolve().parents[2])
            environment["PYTHONPATH"] = package_root + os.pathsep + environment.get("PYTHONPATH", "")
            host_command = [sys.executable, "-m", "testbox.core.host"]
        execution_lock = None
        if not manifest.capabilities["concurrency"]:
            execution_lock = PluginExecutionLock(self.workspace_dir / ".locks" / f"{manifest.name}.lock")
            execution_lock.acquire()
        try:
            process = subprocess.Popen(host_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=self.root, env=environment)
        except Exception:
            if execution_lock: execution_lock.release()
            raise
        self.history.set_host_pid(task_id, process.pid)
        manifest_record = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest_record["host_pid"] = process.pid
        self._write_json(task_dir / "manifest.json", manifest_record)
        try:
            stdout, stderr = process.communicate(json.dumps(request), timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            result = Result("failed", f"插件执行超过 {self.timeout_seconds:g} 秒限制", data={"error_code": "TIMEOUT", "timeout_seconds": self.timeout_seconds})
            self._write_json(task_dir / "result.json", result.to_dict()); self._write_report(task_dir, task_id, manifest, result)
            self.history.finish(task_id, status="FAILED", finished_at=datetime.now(UTC).isoformat(), error_code="TIMEOUT")
            if execution_lock: execution_lock.release()
            return task_id, result
        try:
            event = json.loads(stdout)
            if event.get("protocol_version") != 1 or event.get("event") != "result" or event.get("task_id") != task_id or not isinstance(event.get("result"), dict):
                raise ValueError("响应事件不符合协议")
            payload = event["result"]
        except (json.JSONDecodeError, ValueError, AttributeError):
            payload = {"status": "failed", "message": "插件 Host 协议错误", "data": {"error_code": "HOST_PROTOCOL_ERROR"}, "files": [], "warnings": []}
        if process.returncode and payload.get("status") == "success":
            payload = {"status": "failed", "message": "插件 Host 异常退出", "data": {"error_code": "HOST_CRASHED", "exit_code": process.returncode}, "files": [], "warnings": []}
        result = Result(**payload)
        if result.status == "failed":
            # Surface bounded Host diagnostics in result.json and the CLI so a
            # packaged executable failure is actionable without manually
            # locating the ephemeral PyInstaller extraction directory.
            diagnostics: dict[str, Any] = {"host_exit_code": process.returncode}
            if stderr.strip():
                diagnostics["host_stderr"] = stderr.strip()[-4_000:]
            task_log = task_dir / "logs" / "task.log"
            if task_log.is_file():
                diagnostics["task_log_tail"] = task_log.read_text(encoding="utf-8", errors="replace")[-8_000:]
            result.data = {**result.data, **diagnostics}
        output_size = 0
        for file_name in result.files:
            try: (task_dir / "output" / file_name).resolve().relative_to((task_dir / "output").resolve())
            except ValueError: result = Result("failed", "插件返回了非法输出路径", data={"error_code": "INVALID_OUTPUT_PATH"}); break
            output_file = task_dir / "output" / file_name
            if not output_file.is_file():
                result = Result("failed", "插件声明的输出文件不存在", data={"error_code": "MISSING_OUTPUT_FILE"}); break
            output_size += output_file.stat().st_size
            if output_size > self.MAX_OUTPUT_BYTES:
                result = Result("failed", "插件输出超过大小限制", data={"error_code": "OUTPUT_TOO_LARGE", "max_output_bytes": self.MAX_OUTPUT_BYTES}); break
        self._write_json(task_dir / "result.json", result.to_dict()); self._write_report(task_dir, task_id, manifest, result)
        task_status = {"success": "SUCCEEDED", "failed": "FAILED", "cancelled": "CANCELLED"}[result.status]
        self.history.finish(task_id, status=task_status, finished_at=datetime.now(UTC).isoformat(), error_code=result.data.get("error_code"))
        if execution_lock: execution_lock.release()
        return task_id, result

    def clean_workspace(self, before: date) -> int:
        removed = 0
        if not self.workspace_dir.exists(): return removed
        for task_dir in self.workspace_dir.iterdir():
            if not task_dir.is_dir(): continue
            try: started = datetime.strptime(task_dir.name.split("-", 1)[0], "%Y%m%dT%H%M%S").date()
            except ValueError: continue
            if started < before:
                import shutil
                shutil.rmtree(task_dir); removed += 1
        return removed

    def commit_output(self, task_id: str, relative_path: str, destination: Path) -> Path:
        """Explicitly export one declared task output to a user-selected path."""
        record = self.history.get(task_id)
        if not record:
            raise LookupError("未找到任务")
        result_path = Path(record["result_path"])
        if not result_path.is_file():
            raise ValueError("任务结果不存在")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("status") != "success" or relative_path not in result.get("files", []):
            raise ValueError("只能提交成功任务声明的输出文件")
        output_root = Path(record["workspace_path"]) / "output"
        source = (output_root / relative_path).resolve()
        try:
            source.relative_to(output_root.resolve())
        except ValueError as error:
            raise ValueError("输出路径不合法") from error
        if not source.is_file():
            raise ValueError("输出文件不存在")
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.testbox-{task_id}.tmp")
        shutil.copy2(source, temporary)
        temporary.replace(destination)
        return destination

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp"); temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); temporary.replace(path)

    @staticmethod
    def _write_report(task_dir: Path, task_id: str, manifest: Manifest, result: Result) -> None:
        files = "\n".join(f"- `output/{item}`" for item in result.files) or "- 无"
        (task_dir / "report.md").write_text(f"# TestBox 任务报告\n\n- 任务 ID: `{task_id}`\n- 插件: `{manifest.name}` {manifest.version}\n- 状态: {result.status}\n- 摘要: {result.message}\n\n## 产物\n{files}\n", encoding="utf-8")
