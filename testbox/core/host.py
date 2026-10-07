from __future__ import annotations

import importlib.util, json, sys, threading, traceback
from pathlib import Path

from testbox.core.redaction import Redactor
from testbox.sdk import Context, PluginError, Result, SafeFiles, Task, Workspace


DEFAULT_MAX_LOG_BYTES = 1024 * 1024


class TaskLogger:
    """Redacted UTF-8 logging with a hard per-task disk quota.

    Reserve space for a single truncation notice, including when one log call
    exceeds the entire quota. The lock also covers plugins logging on threads.
    """

    TRUNCATION_NOTICE = "WARNING 日志已达到大小上限，后续内容已截断。\n"

    def __init__(self, path: Path, redactor: Redactor | None = None, *, max_bytes: int = DEFAULT_MAX_LOG_BYTES):
        notice = self.TRUNCATION_NOTICE.encode("utf-8")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < len(notice):
            raise ValueError(f"日志额度必须为至少 {len(notice)} 字节的整数")
        self.path = path
        self.redactor = redactor if redactor is not None else Redactor()
        self.max_bytes = max_bytes
        self._notice = notice
        self._lock = threading.Lock()
        self._truncated = False
        if path.exists():
            with path.open("rb") as existing:
                existing.seek(max(0, path.stat().st_size - len(notice)))
                self._truncated = existing.read() == notice

    def _write(self, level: str, message: str) -> None:
        with self._lock:
            if self._truncated:
                return
            # Redact before slicing so truncation never exposes half a secret.
            content = f"{level} {self.redactor.text(str(message))}\n".encode("utf-8")
            with self.path.open("ab") as log:
                remaining = self.max_bytes - log.tell()
                if remaining <= 0:
                    self._truncated = True
                    return
                available = max(0, remaining - len(self._notice))
                if len(content) <= available:
                    log.write(content)
                    return
                # Never split a multibyte character, even at a tiny quota.
                prefix = content[:available].decode("utf-8", errors="ignore").encode("utf-8")
                if prefix and not prefix.endswith(b"\n"):
                    # Put the notice on its own line without exceeding the
                    # reserved quota (drop one complete character if needed).
                    prefix = prefix.decode("utf-8")[:-1].encode("utf-8") + b"\n"
                log.write(prefix)
                if remaining >= len(self._notice):
                    log.write(self._notice)
                self._truncated = True

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def warning(self, message: str) -> None:
        self._write("WARNING", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)


def main(*, request_path: Path | None = None, response_path: Path | None = None) -> None:
    if (request_path is None) != (response_path is None):
        raise ValueError("Host 文件协议必须同时提供 request 和 response 路径")
    request_text = sys.stdin.read() if request_path is None else request_path.read_text(encoding="utf-8")
    request = json.loads(request_text)
    if request.get("protocol_version") != 1:
        raise ValueError("不支持的 Host 协议版本")
    workspace = Path(request["workspace"]); module_name, class_name = request["entry"].split(":", 1)
    source = Path(request["plugin_path"]) / (module_name.replace(".", "/") + ".py")
    redactor = Redactor(request.get("params", {}), request.get("config", {}))
    logger = TaskLogger(workspace / "logs" / "task.log", redactor); context = Context(logger, request.get("config", {}), Workspace(workspace, workspace / "input", workspace / "output"), SafeFiles(workspace / "output"), Task(request["task_id"]))
    plugin = None
    try:
        spec = importlib.util.spec_from_file_location(f"testbox_plugin_{request['task_id']}", source)
        if spec is None or spec.loader is None: raise RuntimeError("无法加载插件入口")
        module = importlib.util.module_from_spec(spec)
        # Dataclasses and plugins with package-local imports resolve their module
        # metadata through sys.modules during execution.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module); plugin = getattr(module, class_name)()
        plugin.init(context); result = plugin.execute(request["command"], request["params"])
        if not isinstance(result, Result): raise RuntimeError("插件 execute 必须返回 Result")
        payload = result.to_dict()
    except PluginError as error:
        logger.error(str(error)); payload = Result("failed", str(error), data={"error_code": error.code, "details": error.details}).to_dict()
    except Exception as error:
        # Keep the public summary stable, but return a compact, structured
        # diagnostic. This is essential for frozen applications, where the
        # task workspace may be the only place a user can inspect failures.
        logger.error(traceback.format_exc())
        payload = Result(
            "failed",
            "插件执行失败",
            data={
                "error_code": "EXECUTION_FAILED",
                "exception_type": type(error).__name__,
                "exception_message": str(error),
            },
        ).to_dict()
    finally:
        if plugin is not None:
            try: plugin.destroy()
            except Exception: logger.error("插件 destroy 失败")
    # Result status and file paths are machine identities, not diagnostics.
    # Changing a path could orphan a real output; changing status/protocol
    # fields could break the single-result contract when a secret is "success".
    safe_payload = {
        **payload,
        "message": redactor.text(payload["message"]),
        "data": redactor.value(payload["data"]),
        "warnings": redactor.value(payload["warnings"]),
    }
    event = {"protocol_version": 1, "event": "result", "task_id": request["task_id"], "result": safe_payload}
    # Pipe mode stays ASCII-only for legacy Windows code pages. File mode is
    # UTF-8 because it never crosses a console encoding boundary.
    serialized = json.dumps(event, ensure_ascii=request_path is None)
    if response_path is None:
        sys.stdout.write(serialized)
    else:
        response_path.write_text(serialized, encoding="utf-8")


if __name__ == "__main__": main()
