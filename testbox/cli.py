from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from testbox.core.errors import ErrorCode, ExitCode
from testbox.core.manifest import Manifest
from testbox.core.plugin_packages import PluginPackageError, install_plugin, package_plugin, uninstall_plugin
from testbox.core.models import TaskStatus
from testbox.core.runtime import Runtime


class CliFailure(Exception):
    def __init__(self, code: str, message: str, exit_code: int, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details or {}


def configure_console_unicode() -> None:
    """Use UTF-8 for CLI text when a Windows console exposes a legacy code page."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):
            pass


def parse_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_params(values: list[str], params_file: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if params_file:
        try:
            loaded = json.loads(Path(params_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CliFailure(ErrorCode.INVALID_PARAMS, f"无法读取参数文件: {error}", int(ExitCode.USAGE)) from error
        if not isinstance(loaded, dict):
            raise CliFailure(ErrorCode.INVALID_PARAMS, "参数文件必须是 JSON 对象", int(ExitCode.USAGE))
        result.update(loaded)
    for item in values:
        if "=" not in item:
            raise CliFailure(ErrorCode.INVALID_PARAMS, "--set 必须为 key=value", int(ExitCode.USAGE))
        key, value = item.split("=", 1)
        if not key or key in result:
            raise CliFailure(ErrorCode.INVALID_PARAMS, f"重复或无效参数: {key}", int(ExitCode.USAGE))
        result[key] = parse_value(value)
    return result


def parse_scalar_options(values: list[str]) -> list[str]:
    """Convert schema-derived CLI options such as --count 10 into --set pairs."""
    pairs: list[str] = []
    index = 0
    while index < len(values):
        option = values[index]
        if not option.startswith("--") or option == "--" or index + 1 >= len(values):
            raise CliFailure(ErrorCode.INVALID_PARAMS, f"无法识别的参数: {option}", int(ExitCode.USAGE))
        pairs.append(f"{option[2:].replace('-', '_')}={values[index + 1]}")
        index += 2
    return pairs


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 输出")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="testbox")
    _json_flag(parser)
    sub = parser.add_subparsers(dest="action", required=True)

    gui = sub.add_parser("gui")
    _json_flag(gui)

    plugin = sub.add_parser("plugin")
    _json_flag(plugin)
    plugin_sub = plugin.add_subparsers(dest="plugin_action", required=True)
    plugin_list = plugin_sub.add_parser("list")
    _json_flag(plugin_list)
    inspect = plugin_sub.add_parser("inspect")
    inspect.add_argument("identifier")
    _json_flag(inspect)
    validate = plugin_sub.add_parser("validate")
    validate.add_argument("path")
    _json_flag(validate)
    install = plugin_sub.add_parser("install")
    install.add_argument("path")
    install.add_argument("--force", action="store_true")
    _json_flag(install)
    uninstall = plugin_sub.add_parser("uninstall")
    uninstall.add_argument("name")
    _json_flag(uninstall)
    package = plugin_sub.add_parser("package")
    package.add_argument("path")
    package.add_argument("--output", required=True)
    _json_flag(package)

    run = sub.add_parser("run")
    run.add_argument("command")
    run.add_argument("--set", action="append", default=[])
    run.add_argument("--params-file")
    _json_flag(run)

    task = sub.add_parser("task")
    _json_flag(task)
    task_sub = task.add_subparsers(dest="task_action", required=True)
    show = task_sub.add_parser("show")
    show.add_argument("task_id")
    _json_flag(show)
    task_list = task_sub.add_parser("list")
    task_list.add_argument("--status", choices=[item.value for item in TaskStatus])
    task_list.add_argument("--command")
    task_list.add_argument("--limit", type=int, default=100)
    task_list.add_argument("--offset", type=int, default=0)
    _json_flag(task_list)
    result = task_sub.add_parser("result")
    result.add_argument("task_id")
    _json_flag(result)
    export = task_sub.add_parser("export")
    export.add_argument("task_id")
    export.add_argument("relative_path", nargs="?", default=None, help="相对路径（如导出全部为zip压缩包可省略并搭配 --archive）")
    export.add_argument("--output", required=True)
    export.add_argument("--archive", "--zip", action="store_true", help="将任务的所有产物文件打包为 ZIP 格式导出，保持目录层级结构一致")
    _json_flag(export)

    workspace = sub.add_parser("workspace")
    _json_flag(workspace)
    workspace_sub = workspace.add_subparsers(dest="workspace_action", required=True)
    clean = workspace_sub.add_parser("clean")
    clean.add_argument("--before", required=True)
    clean.add_argument("--confirm", action="store_true")
    _json_flag(clean)
    return parser


def _want_json(arguments: argparse.Namespace) -> bool:
    return bool(getattr(arguments, "json", False))


def emit_error(error: CliFailure, *, as_json: bool) -> None:
    payload: dict[str, Any] = {"ok": False, "error": {"code": error.code, "message": error.message}}
    if error.details:
        payload["error"]["details"] = error.details
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(f"[{error.code}] {error.message}", file=sys.stderr)


def emit_value(value: Any, *, as_json: bool, text: str | None = None) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif text is not None:
        print(text)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def _task_not_found(task_id: str) -> CliFailure:
    return CliFailure(ErrorCode.TASK_NOT_FOUND, f"未找到任务: {task_id}", int(ExitCode.USAGE))


def main() -> None:
    configure_console_unicode()
    if len(sys.argv) == 2 and sys.argv[1] == "--plugin-host":
        from testbox.core.host import main as host_main
        host_main()
        return

    parser = build_parser()
    arguments, unknown = parser.parse_known_args()
    as_json = _want_json(arguments)
    if unknown and arguments.action != "run":
        error = CliFailure(ErrorCode.INVALID_PARAMS, f"无法识别的参数: {' '.join(unknown)}", int(ExitCode.USAGE))
        emit_error(error, as_json=as_json)
        raise SystemExit(error.exit_code)

    runtime: Runtime | None = None
    try:
        if arguments.action == "gui":
            from testbox.gui import main as gui_main
            gui_main()
            return

        runtime = Runtime()

        if arguments.action == "plugin" and arguments.plugin_action == "list":
            commands = [
                {"command": command, "plugin": manifest.name, "version": manifest.version, "status": "available"}
                for command, manifest in runtime.list_commands().items()
            ]
            unavailable = [
                {"path": path, "status": "unavailable", "reason": reason}
                for path, reason in runtime.manager.unavailable.items()
            ]
            payload = {"commands": commands, "unavailable": unavailable}
            text = "\n".join(f"{item['command']}\t{item['plugin']}\t{item['version']}\t可用" for item in commands)
            if unavailable:
                text += ("\n" if text else "") + "\n".join(f"{item['path']}\t-\t-\t不可用: {item['reason']}" for item in unavailable)
            emit_value(payload, as_json=as_json, text=text)
            return

        if arguments.action == "plugin" and arguments.plugin_action == "inspect":
            info = runtime.inspect_plugin(arguments.identifier)
            if info is None:
                raise CliFailure(ErrorCode.PLUGIN_INVALID, f"未找到插件或命令: {arguments.identifier}", int(ExitCode.USAGE))
            emit_value(info, as_json=as_json)
            return

        if arguments.action == "plugin" and arguments.plugin_action == "validate":
            try:
                manifest = Manifest.load(Path(arguments.path) / "manifest.yaml")
            except Exception as error:
                raise CliFailure(ErrorCode.PLUGIN_INVALID, str(error), int(ExitCode.PLUGIN)) from error
            emit_value({"valid": True, "name": manifest.name, "version": manifest.version}, as_json=as_json, text=f"有效: {manifest.name} {manifest.version}")
            return

        if arguments.action == "plugin":
            try:
                if arguments.plugin_action == "package":
                    output = package_plugin(Path(arguments.path), Path(arguments.output))
                    emit_value({"operation": "package", "path": str(output)}, as_json=as_json, text=f"已打包: {output}")
                elif arguments.plugin_action == "install":
                    manifest = install_plugin(Path(arguments.path), runtime.plugins_dir, force=arguments.force)
                    emit_value({"operation": "install", "name": manifest.name, "version": manifest.version}, as_json=as_json, text=f"已安装: {manifest.name} {manifest.version}")
                elif arguments.plugin_action == "uninstall":
                    uninstall_plugin(arguments.name, runtime.plugins_dir)
                    emit_value({"operation": "uninstall", "name": arguments.name}, as_json=as_json, text=f"已卸载: {arguments.name}")
            except (PluginPackageError, ValueError, OSError) as error:
                code = {
                    "package": ErrorCode.PLUGIN_PACKAGE_FAILED,
                    "install": ErrorCode.PLUGIN_INSTALL_FAILED,
                    "uninstall": ErrorCode.PLUGIN_UNINSTALL_FAILED,
                }[arguments.plugin_action]
                raise CliFailure(code, str(error), int(ExitCode.PLUGIN)) from error
            return

        if arguments.action == "task":
            if arguments.task_action == "list":
                if arguments.limit < 0 or arguments.offset < 0:
                    raise CliFailure(ErrorCode.INVALID_PARAMS, "limit 和 offset 不能为负数", int(ExitCode.USAGE))
                tasks = runtime.list_tasks(status=arguments.status, command=arguments.command, limit=arguments.limit, offset=arguments.offset)
                payload = {"tasks": tasks, "count": runtime.count_tasks(status=arguments.status, command=arguments.command), "limit": arguments.limit, "offset": arguments.offset}
                emit_value(payload, as_json=as_json)
                return
            task_record = runtime.get_task(arguments.task_id)
            if not task_record:
                raise _task_not_found(arguments.task_id)
            if arguments.task_action == "show":
                emit_value({"task": task_record, "result": runtime.get_task_result(arguments.task_id)}, as_json=as_json)
                return
            if arguments.task_action == "result":
                result = runtime.get_task_result(arguments.task_id)
                if result is None:
                    raise CliFailure(ErrorCode.RESULT_NOT_FOUND, f"任务结果不存在: {arguments.task_id}", int(ExitCode.FAILED))
                emit_value(result, as_json=as_json)
                return
            if arguments.task_action == "export":
                try:
                    if arguments.archive or arguments.relative_path is None:
                        destination = runtime.commit_outputs_archive(arguments.task_id, Path(arguments.output))
                        emit_value({"task_id": arguments.task_id, "archive": True, "destination": str(destination)}, as_json=as_json, text=f"已打包导出: {destination}")
                    else:
                        destination = runtime.commit_output(arguments.task_id, arguments.relative_path, Path(arguments.output))
                        emit_value({"task_id": arguments.task_id, "relative_path": arguments.relative_path, "destination": str(destination)}, as_json=as_json, text=f"已导出: {destination}")
                except (ValueError, LookupError, OSError) as error:
                    raise CliFailure(ErrorCode.INVALID_PARAMS, str(error), int(ExitCode.USAGE)) from error
                return

        if arguments.action == "workspace":
            if not arguments.confirm:
                raise CliFailure(ErrorCode.INVALID_PARAMS, "清理需要显式确认：请增加 --confirm", int(ExitCode.USAGE))
            try:
                before = date.fromisoformat(arguments.before)
            except ValueError as error:
                raise CliFailure(ErrorCode.INVALID_PARAMS, "日期必须为 YYYY-MM-DD", int(ExitCode.USAGE)) from error
            removed = runtime.clean_workspace(before)
            emit_value({"removed": removed, "before": arguments.before}, as_json=as_json, text=f"已清理 {removed} 个任务工作区")
            return

        params = parse_params(arguments.set + parse_scalar_options(unknown), arguments.params_file)
        try:
            task_id, result = runtime.run(arguments.command, params)
        except LookupError as error:
            raise CliFailure(ErrorCode.COMMAND_NOT_FOUND, str(error), int(ExitCode.USAGE)) from error
        except ValueError as error:
            raise CliFailure(ErrorCode.INVALID_PARAMS, str(error), int(ExitCode.USAGE)) from error
        payload = {"task_id": task_id, "status": result.status, "message": result.message, "data": result.data, "files": result.files, "warnings": result.warnings, "workspace": str(runtime.workspace_dir / task_id)}
        if as_json:
            emit_value(payload, as_json=True)
        else:
            print(f"任务 {task_id}: {result.status} — {result.message}")
            print(f"工作区: {runtime.workspace_dir / task_id}")
            if result.status == "failed":
                diagnostics = {key: result.data[key] for key in ("error_code", "exception_type", "exception_message", "host_exit_code", "host_stderr", "task_log_tail") if key in result.data}
                if diagnostics:
                    print("失败诊断:")
                    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
        raise SystemExit(int(ExitCode.SUCCESS if result.status == "success" else ExitCode.CANCELLED if result.status == "cancelled" else ExitCode.FAILED))
    except CliFailure as error:
        emit_error(error, as_json=as_json)
        raise SystemExit(error.exit_code)
    except KeyboardInterrupt:
        error = CliFailure("CANCELLED", "用户取消了操作", int(ExitCode.CANCELLED))
        emit_error(error, as_json=as_json)
        raise SystemExit(error.exit_code)
    except Exception as error:
        failure = CliFailure(ErrorCode.INTERNAL_ERROR, str(error), int(ExitCode.FAILED))
        emit_error(failure, as_json=as_json)
        raise SystemExit(failure.exit_code)
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    main()
