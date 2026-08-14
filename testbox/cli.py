from __future__ import annotations

import argparse, json
from datetime import date
from pathlib import Path
from typing import Any

from testbox.core.manifest import Manifest
from testbox.core.plugin_packages import PluginPackageError, install_plugin, package_plugin, uninstall_plugin
from testbox.core.runtime import Runtime


def parse_value(value: str) -> Any:
    try: return json.loads(value)
    except json.JSONDecodeError: return value


def parse_params(values: list[str], params_file: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if params_file:
        loaded = json.loads(Path(params_file).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict): raise ValueError("参数文件必须是 JSON 对象")
        result.update(loaded)
    for item in values:
        if "=" not in item: raise ValueError("--set 必须为 key=value")
        key, value = item.split("=", 1)
        if not key or key in result: raise ValueError(f"重复或无效参数: {key}")
        result[key] = parse_value(value)
    return result


def parse_scalar_options(values: list[str]) -> list[str]:
    """Convert schema-derived CLI options such as --count 10 into --set pairs."""
    pairs: list[str] = []
    index = 0
    while index < len(values):
        option = values[index]
        if not option.startswith("--") or option == "--" or index + 1 >= len(values):
            raise ValueError(f"无法识别的参数: {option}")
        pairs.append(f"{option[2:].replace('-', '_')}={values[index + 1]}")
        index += 2
    return pairs


def main() -> None:
    if len(__import__("sys").argv) == 2 and __import__("sys").argv[1] == "--plugin-host":
        from testbox.core.host import main as host_main
        host_main()
        return
    parser = argparse.ArgumentParser(prog="testbox")
    sub = parser.add_subparsers(dest="action", required=True)
    plugin = sub.add_parser("plugin"); plugin_sub = plugin.add_subparsers(dest="plugin_action", required=True); plugin_sub.add_parser("list"); validate = plugin_sub.add_parser("validate"); validate.add_argument("path")
    install = plugin_sub.add_parser("install"); install.add_argument("path"); install.add_argument("--force", action="store_true")
    uninstall = plugin_sub.add_parser("uninstall"); uninstall.add_argument("name")
    package = plugin_sub.add_parser("package"); package.add_argument("path"); package.add_argument("--output", required=True)
    run = sub.add_parser("run"); run.add_argument("command"); run.add_argument("--set", action="append", default=[]); run.add_argument("--params-file")
    task = sub.add_parser("task"); task_sub = task.add_subparsers(dest="task_action", required=True); show = task_sub.add_parser("show"); show.add_argument("task_id")
    workspace = sub.add_parser("workspace"); workspace_sub = workspace.add_subparsers(dest="workspace_action", required=True); clean = workspace_sub.add_parser("clean"); clean.add_argument("--before", required=True); clean.add_argument("--confirm", action="store_true")
    arguments, unknown = parser.parse_known_args(); runtime = Runtime()
    if unknown and arguments.action != "run": parser.error(f"无法识别的参数: {' '.join(unknown)}")
    if arguments.action == "plugin" and arguments.plugin_action == "list":
        for command, manifest in runtime.manager.available.items(): print(f"{command}\t{manifest.name}\t{manifest.version}\t可用")
        for path, reason in runtime.manager.unavailable.items(): print(f"{path}\t-\t-\t不可用: {reason}")
        return
    if arguments.action == "plugin" and arguments.plugin_action == "validate":
        try: manifest = Manifest.load(Path(arguments.path) / "manifest.yaml"); print(f"有效: {manifest.name} {manifest.version}")
        except Exception as error: print(f"无效: {error}"); raise SystemExit(3)
        return
    if arguments.action == "plugin":
        try:
            if arguments.plugin_action == "package":
                output = package_plugin(Path(arguments.path), Path(arguments.output)); print(f"已打包: {output}")
            elif arguments.plugin_action == "install":
                manifest = install_plugin(Path(arguments.path), runtime.plugins_dir, force=arguments.force); print(f"已安装: {manifest.name} {manifest.version}")
            elif arguments.plugin_action == "uninstall":
                uninstall_plugin(arguments.name, runtime.plugins_dir); print(f"已卸载: {arguments.name}")
        except (PluginPackageError, ValueError) as error: print(f"插件操作失败: {error}"); raise SystemExit(3)
        return
    if arguments.action == "task":
        task_record = runtime.history.get(arguments.task_id)
        if not task_record: print("未找到任务"); raise SystemExit(2)
        result_path = Path(task_record["result_path"])
        result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else None
        print(json.dumps({"task": task_record, "result": result}, ensure_ascii=False, indent=2)); return
    if arguments.action == "workspace":
        if not arguments.confirm: print("清理需要显式确认：请增加 --confirm"); raise SystemExit(2)
        try: before = date.fromisoformat(arguments.before)
        except ValueError: print("日期必须为 YYYY-MM-DD"); raise SystemExit(2)
        print(f"已清理 {runtime.clean_workspace(before)} 个任务工作区"); return
    try:
        task_id, result = runtime.run(arguments.command, parse_params(arguments.set + parse_scalar_options(unknown), arguments.params_file))
        print(f"任务 {task_id}: {result.status} — {result.message}"); print(f"工作区: {runtime.workspace_dir / task_id}")
        raise SystemExit(0 if result.status == "success" else 130 if result.status == "cancelled" else 4)
    except (ValueError, LookupError) as error: print(f"参数或命令错误: {error}"); raise SystemExit(2)


if __name__ == "__main__": main()
