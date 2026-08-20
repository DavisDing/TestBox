from __future__ import annotations

import importlib.util, json, sys, traceback
from pathlib import Path

from testbox.sdk import Context, PluginError, Result, SafeFiles, Task, Workspace


class TaskLogger:
    def __init__(self, path: Path): self.path = path
    def info(self, message: str) -> None: self.path.open("a", encoding="utf-8").write(f"INFO {message}\n")
    def warning(self, message: str) -> None: self.path.open("a", encoding="utf-8").write(f"WARNING {message}\n")
    def error(self, message: str) -> None: self.path.open("a", encoding="utf-8").write(f"ERROR {message}\n")


def main() -> None:
    request = json.loads(sys.stdin.read())
    if request.get("protocol_version") != 1:
        raise ValueError("不支持的 Host 协议版本")
    workspace = Path(request["workspace"]); module_name, class_name = request["entry"].split(":", 1)
    source = Path(request["plugin_path"]) / (module_name.replace(".", "/") + ".py")
    logger = TaskLogger(workspace / "logs" / "task.log"); context = Context(logger, request.get("config", {}), Workspace(workspace, workspace / "input", workspace / "output"), SafeFiles(workspace / "output"), Task(request["task_id"]))
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
    event = {"protocol_version": 1, "event": "result", "task_id": request["task_id"], "result": payload}
    # The host protocol travels through subprocess pipes.  Keep it ASCII-only so
    # Windows runners using a legacy code page can reliably decode the response;
    # json.loads restores the original Unicode strings in the parent process.
    sys.stdout.write(json.dumps(event, ensure_ascii=True))


if __name__ == "__main__": main()
