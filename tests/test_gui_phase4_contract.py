"""Static GUI Phase 4 contracts.

The suite inspects :mod:`testbox.gui` as source and AST instead of importing it.
PySide6 is optional, so these tests must remain runnable in a plain Core/Runtime
test environment.  The contracts protect the Phase 4 history, diagnostics, and
read-only settings boundaries described in
``docs/proposals/gui-information-architecture.md``.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI_PATH = ROOT / "testbox" / "gui.py"


class GuiPhase4Source:
    """Small AST/source adapter that never imports the optional Qt module."""

    def __init__(self, path: Path = GUI_PATH):
        self.path = path
        self.source = path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source, filename=str(path))

    def class_node(self, name: str) -> ast.ClassDef:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == name:
                return node
        raise AssertionError(f"未找到 GUI 类: {name}")

    def method(self, class_name: str, method_name: str) -> ast.FunctionDef:
        owner = self.class_node(class_name)
        for node in owner.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name:
                return node
        raise AssertionError(f"{class_name} 未找到方法: {method_name}")

    def methods(self, class_name: str) -> list[ast.FunctionDef]:
        owner = self.class_node(class_name)
        return [
            node
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

    def segment(self, node: ast.AST) -> str:
        value = ast.get_source_segment(self.source, node)
        if value is None:
            raise AssertionError(f"无法读取 AST 节点源码: {type(node).__name__}")
        return value

    @staticmethod
    def calls(node: ast.AST) -> list[ast.Call]:
        return [item for item in ast.walk(node) if isinstance(item, ast.Call)]

    @staticmethod
    def attribute_call_names(node: ast.AST) -> list[str]:
        return [
            call.func.attr
            for call in GuiPhase4Source.calls(node)
            if isinstance(call.func, ast.Attribute)
        ]

    @staticmethod
    def self_runtime_call_names(node: ast.AST) -> set[str]:
        names: set[str] = set()
        for call in GuiPhase4Source.calls(node):
            func = call.func
            if not isinstance(func, ast.Attribute):
                continue
            owner = func.value
            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "self"
                and owner.attr == "runtime"
            ):
                names.add(func.attr)
        return names

    @staticmethod
    def call_lines(node: ast.AST, attribute_name: str) -> list[int]:
        return sorted(
            call.lineno
            for call in GuiPhase4Source.calls(node)
            if isinstance(call.func, ast.Attribute) and call.func.attr == attribute_name
        )


class GuiPhase4ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gui = GuiPhase4Source()

    def test_history_queries_only_through_runtime_and_gui_does_not_connect_sqlite(self):
        """History reads stay behind Runtime and the GUI never opens SQLite itself."""
        history = self.gui.class_node("TaskHistoryView")
        refresh = self.gui.method("TaskHistoryView", "refresh_data")
        runtime_calls = self.gui.self_runtime_call_names(refresh)
        self.assertIn("list_tasks", runtime_calls)
        self.assertIn(
            "count_tasks",
            runtime_calls,
            "分页总数也应由 Runtime 提供，不能由 GUI 查询数据库",
        )

        direct_history_access = [
            item
            for item in ast.walk(history)
            if isinstance(item, ast.Attribute)
            and isinstance(item.value, ast.Attribute)
            and isinstance(item.value.value, ast.Name)
            and item.value.value.id == "self"
            and item.value.attr == "runtime"
            and item.attr in {"history", "connection", "db", "database"}
        ]
        self.assertFalse(direct_history_access, "TaskHistoryView 不得穿透 Runtime 访问历史存储对象")

        sqlite_imports = [
            node
            for node in ast.walk(self.gui.tree)
            if (
                isinstance(node, ast.Import)
                and any(alias.name in {"sqlite3", "apsw"} for alias in node.names)
            )
            or (
                isinstance(node, ast.ImportFrom)
                and node.module in {"sqlite3", "apsw"}
            )
        ]
        sqlite_connects = [
            call
            for call in self.gui.calls(self.gui.tree)
            if isinstance(call.func, ast.Attribute)
            and call.func.attr in {"connect", "cursor", "execute", "executemany", "executescript"}
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in {"sqlite3", "apsw", "connection", "conn", "cursor"}
        ]
        self.assertFalse(sqlite_imports, "GUI 不得导入 SQLite 客户端")
        self.assertFalse(sqlite_connects, "GUI 不得建立 SQLite 连接或执行 SQL")

    def test_history_has_separate_loading_empty_and_error_states(self):
        """Loading, Empty, and Error are explicit peer states, not one blank table."""
        history = self.gui.class_node("TaskHistoryView")
        class_source = self.gui.segment(history)
        markers = {
            "loading": re.compile(r"loading|加载中|正在加载", re.I),
            "empty": re.compile(r"empty|暂无(?:任务|历史|记录)|无历史", re.I),
            "error": re.compile(r"error|加载失败|读取失败|查询失败", re.I),
        }
        for state, pattern in markers.items():
            self.assertRegex(class_source, pattern, f"任务历史缺少独立的 {state} 状态标识")

        named_targets = {
            state: bool(
                re.search(
                    rf"(?:self\.)?[A-Za-z_]*(?:{state}|{'加载' if state == 'loading' else '空' if state == 'empty' else '错误'})[A-Za-z_]*\s*=",
                    class_source,
                    re.I,
                )
            )
            for state in markers
        }
        controller_has_all_states = any(
            all(pattern.search(self.gui.segment(method)) for pattern in markers.values())
            for method in self.gui.methods("TaskHistoryView")
        )
        self.assertTrue(
            all(named_targets.values()) or controller_has_all_states,
            "任务历史应使用三个独立状态控件，或由同一状态控制方法显式切换 loading/empty/error",
        )

    def test_history_distinguishes_no_records_from_filtered_no_results(self):
        """An empty database and an over-restrictive filter require different help."""
        history_source = self.gui.segment(self.gui.class_node("TaskHistoryView"))
        self.assertRegex(
            history_source,
            r"暂无(?:任务|历史)(?:记录)?|尚无(?:任务|历史)(?:记录)?|还没有任务",
            "没有任何历史记录时应显示初始空状态",
        )
        self.assertRegex(
            history_source,
            r"暂无符合条件|筛选(?:条件)?.*(?:无结果|没有结果|未找到)|未找到.*(?:任务|记录)",
            "筛选无结果时应提示调整或清除筛选条件",
        )

        refresh_source = self.gui.segment(self.gui.method("TaskHistoryView", "refresh_data"))
        has_filter_branch = bool(
            re.search(r"\bif\s+(?:query|status|command|has_filter|filters?_active)\b", refresh_source)
            or re.search(r"\b(?:query|status|command)\s+(?:or|and)\s+(?:query|status|command)\b", refresh_source)
        )
        self.assertTrue(has_filter_branch, "空状态选择必须判断任务 ID、状态或命令筛选是否生效")

    def test_reexecute_only_prefills_and_navigates_to_workbench(self):
        """Historical re-execution is user-authorized in the workbench, never automatic."""
        reexecute = self.gui.method("TaskResultDetailView", "_on_re_execute")
        reexecute_calls = set(self.gui.attribute_call_names(reexecute))
        self.assertIn("emit", reexecute_calls, "重新执行入口应发出命令与安全参数的回填信号")
        self.assertFalse(
            reexecute_calls & {"run", "execute", "execute_task", "start", "start_running"},
            "任务详情的重新执行入口不得自动提交任务",
        )

        main_init = self.gui.method("MainWindow", "_init_ui")
        main_source = self.gui.segment(main_init)
        self.assertRegex(
            main_source,
            r"reExecuteRequested\.connect\(self\.navigate_to_command\)",
            "重新执行信号应导航并回填命令工作台",
        )
        self.assertNotRegex(
            main_source,
            r"reExecuteRequested\.connect\(self\.execute_task\)",
            "重新执行信号不得直接连接任务提交入口",
        )

        navigate = self.gui.method("MainWindow", "navigate_to_command")
        navigate_calls = set(self.gui.attribute_call_names(navigate))
        self.assertIn("load_command", navigate_calls)
        self.assertFalse(
            navigate_calls & {"run", "execute", "execute_task", "start_running"},
            "工作台导航只能加载参数，不能隐式执行",
        )

    def test_history_cleanup_uses_runtime_after_explicit_confirmation(self):
        """Workspace cleanup remains a confirmed Runtime-owned high-impact action."""
        cleanup = self.gui.method("TaskHistoryView", "_on_clean_workspace")
        source = self.gui.segment(cleanup)
        runtime_calls = self.gui.self_runtime_call_names(cleanup)
        self.assertIn("clean_workspace", runtime_calls)
        self.assertFalse(
            set(self.gui.attribute_call_names(cleanup)) & {"rmtree", "unlink", "remove", "removedirs"},
            "GUI 不得自行删除任务工作区文件",
        )
        self.assertRegex(source, r"确认清理|是否继续|永久删除|永久清理")
        self.assertTrue(
            "QMessageBox.question" in source
            or ("QDialog" in source and re.search(r"dialog\.exec\(\)|dialog\.exec_\(\)", source)),
            "清理工作区必须有明确、可取消的确认步骤",
        )

    def test_plugin_install_and_uninstall_use_runtime_with_confirmation(self):
        """Plugin package mutations are delegated to Runtime and confirmed first."""
        cases = (
            ("_on_import_plugin", "install_plugin"),
            ("_on_uninstall_plugin", "uninstall_plugin"),
        )
        for method_name, mutation in cases:
            with self.subTest(method=method_name):
                method = self.gui.method("PluginDiagnosticsView", method_name)
                runtime_calls = self.gui.self_runtime_call_names(method)
                self.assertIn(mutation, runtime_calls)

                confirmation_lines = self.gui.call_lines(method, "question")
                mutation_lines = self.gui.call_lines(method, mutation)
                self.assertTrue(confirmation_lines, f"{method_name} 缺少用户确认对话框")
                self.assertTrue(mutation_lines, f"{method_name} 未调用 Runtime.{mutation}")
                self.assertLess(
                    min(confirmation_lines),
                    min(mutation_lines),
                    f"Runtime.{mutation} 必须发生在用户明确确认之后",
                )

    def test_plugin_diagnostics_separates_available_and_unavailable_plugins(self):
        """Diagnostics presents healthy plugins and problem plugins separately."""
        diagnostics = self.gui.class_node("PluginDiagnosticsView")
        source = self.gui.segment(diagnostics)
        refresh = self.gui.method("PluginDiagnosticsView", "refresh_plugins")
        self.assertIn("list_plugins", self.gui.self_runtime_call_names(refresh))
        self.assertRegex(source, r"可用插件|available(?:_plugins?|_section|_table)?", "缺少可用插件分区")
        self.assertRegex(source, r"不可用插件|问题插件|invalid_plugins?|unavailable", "缺少不可用/问题插件分区")
        self.assertRegex(source, r"原因|reason|manifest|Schema|兼容", "问题插件必须展示具体不可用原因")

    def test_plugin_diagnostics_shows_version_commands_capabilities_and_path(self):
        """The read-only plugin inventory exposes the fields needed for diagnosis."""
        source = self.gui.segment(self.gui.class_node("PluginDiagnosticsView"))
        required = {
            "version": r"版本|version",
            "commands": r"命令|commands?",
            "capabilities": r"能力|capabilit|concurrency|filesystem|network|resources",
            "path": r"路径|目录|path|plugins_dir",
        }
        for field, pattern in required.items():
            with self.subTest(field=field):
                self.assertRegex(source, pattern, f"插件诊断缺少 {field} 信息")

    def test_settings_are_read_only_runtime_and_environment_diagnostics(self):
        """Phase 4 settings expose paths/version/diagnostics without new controls."""
        settings = self.gui.class_node("SettingsDialog")
        source = self.gui.segment(settings)
        required = {
            "runtime path": r"self\.runtime\.(?:root|runtime_dir|base_dir)",
            "workspace path": r"工作区|Workspace|workspace_dir",
            "plugin path": r"插件目录|Plugins|plugins_dir",
            "version": r"版本|Version|__version__",
            "read-only diagnostics": r"只读|诊断|操作系统|Python 环境|sys\.platform|sys\.version",
        }
        for field, pattern in required.items():
            with self.subTest(field=field):
                self.assertRegex(source, pattern, f"设置页缺少 {field} 展示")

        mutating_runtime_calls = self.gui.self_runtime_call_names(settings) & {
            "enable_plugin",
            "disable_plugin",
            "set_plugin_enabled",
            "grant_permission",
            "revoke_permission",
            "set_permissions",
            "create_plugin_environment",
            "create_virtualenv",
            "set_secret",
            "save_secret",
            "set_config_reference",
        }
        self.assertFalse(mutating_runtime_calls, "设置页第一阶段只能展示只读信息")

    def test_settings_do_not_implement_unconfirmed_capabilities(self):
        """Unconfirmed enablement/security/environment features stay out of Phase 4."""
        source = self.gui.segment(self.gui.class_node("SettingsDialog"))
        forbidden_text = {
            "plugin enable/disable": r"插件启用|插件禁用|启用插件|禁用插件|enable[_ -]?plugin|disable[_ -]?plugin",
            "permission system": r"权限授权|权限确认|权限管理|grant[_ -]?permission|revoke[_ -]?permission",
            "isolated environment": r"独立依赖环境|独立虚拟环境|plugin[_ -]?(?:venv|environment)|create[_ -]?virtualenv",
            "keychain": r"钥匙串|密钥链|keychain|keyring",
            "config references": r"配置引用|config(?:uration)?[_ -]?ref(?:erence)?",
        }
        for capability, pattern in forbidden_text.items():
            with self.subTest(capability=capability):
                self.assertNotRegex(source, pattern, f"设置页提前实现了未确认能力: {capability}")

        settings = self.gui.class_node("SettingsDialog")
        interactive_controls = {
            call.func.attr
            for call in self.gui.calls(settings)
            if isinstance(call.func, ast.Attribute)
            and call.func.attr in {"QCheckBox", "QComboBox", "QLineEdit", "QSpinBox", "QRadioButton"}
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "QtWidgets"
        }
        self.assertFalse(
            interactive_controls,
            f"设置页第一阶段不应出现可编辑配置控件: {sorted(interactive_controls)}",
        )


if __name__ == "__main__":
    unittest.main()
