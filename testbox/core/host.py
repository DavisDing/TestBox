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
    request = json.loads(sys.stdin.read()); workspace = Path(request["workspace"]); module_name, class_name = request["entry"].split(":", 1)
    source = Path(request["plugin_path"]) / (module_name.replace(".", "/") + ".py")
    logger = TaskLogger(workspace / "logs" / "task.log"); context = Context(logger, {}, Workspace(workspace, workspace / "input", workspace / "output"), SafeFiles(workspace / "output"), Task(request["task_id"]))
    plugin = None
    try:
        spec = importlib.util.spec_from_file_location(f"testbox_plugin_{request['task_id']}", source)
        if spec is None or spec.loader is None: raise RuntimeError("无法加载插件入口")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); plugin = getattr(module, class_name)()
        plugin.init(context); result = plugin.execute(request["command"], request["params"])
        if not isinstance(result, Result): raise RuntimeError("插件 execute 必须返回 Result")
        payload = result.to_dict()
    except PluginError as error:
        logger.error(str(error)); payload = Result("failed", str(error), data={"error_code": error.code, "details": error.details}).to_dict()
    except Exception:
        logger.error(traceback.format_exc()); payload = Result("failed", "插件执行失败", data={"error_code": "EXECUTION_FAILED"}).to_dict()
    finally:
        if plugin is not None:
            try: plugin.destroy()
            except Exception: logger.error("插件 destroy 失败")
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__": main()
