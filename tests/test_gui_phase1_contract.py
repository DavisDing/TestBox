"""Static GUI Phase 1 contracts.

These tests intentionally inspect ``testbox/gui.py`` as source instead of importing
it.  PySide6 is optional, so the contract suite must remain runnable in a plain
Runtime/test environment.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI_PATH = ROOT / "testbox" / "gui.py"


class GuiSourceContract:
    """Small AST/source adapter used by the GUI contract tests."""

    def __init__(self, path: Path = GUI_PATH):
        self.path = path
        self.source = path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source, filename=str(path))
        self.catalog = self._find_class("ToolCatalogView")

    def _find_class(self, name: str) -> ast.ClassDef:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == name:
                return node
        raise AssertionError(f"未找到 GUI 类: {name}")

    def method(self, name: str) -> ast.FunctionDef:
        for node in self.catalog.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        raise AssertionError(f"ToolCatalogView 未找到方法: {name}")

    def segment(self, node: ast.AST) -> str:
        segment = ast.get_source_segment(self.source, node)
        if segment is None:
            raise AssertionError(f"无法读取 AST 节点源码: {type(node).__name__}")
        return segment

    def call_nodes(self, node: ast.AST | None = None) -> list[ast.Call]:
        return [item for item in ast.walk(node or self.catalog) if isinstance(item, ast.Call)]


class GuiPhase1ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = GuiSourceContract()

    def test_tool_catalog_has_loading_state_branch(self):
        """目录加载不能只显示旧卡片，必须有可识别的 Loading 状态。"""
        source = self.contract.segment(self.contract.catalog)
        has_loading_marker = bool(
            re.search(r"loading(?:_widget|_label|_state|_overlay)?|加载中|正在加载|Loading", source, re.I)
        )
        self.assertTrue(
            has_loading_marker,
            "ToolCatalogView 应声明 Loading 状态控件或用户可见的加载中文案",
        )

    def test_tool_catalog_has_runtime_error_state_branch(self):
        """Runtime.list_commands 失败时目录应进入 Error 状态，而不是让窗口崩溃。"""
        reload_tools = self.contract.method("reload_tools")
        source = self.contract.segment(reload_tools)
        has_try_except = any(isinstance(node, ast.Try) for node in ast.walk(reload_tools))
        has_error_marker = bool(
            re.search(r"error(?:_widget|_label|_state)?|加载失败|读取失败|Error", source, re.I)
        )
        self.assertTrue(has_try_except, "reload_tools 应捕获 Runtime 目录读取异常")
        self.assertTrue(
            has_error_marker,
            "reload_tools 应更新 Error 状态控件或显示可理解的加载失败文案",
        )

    def test_tool_catalog_has_empty_state_branch(self):
        """目录没有结果时必须有 Empty 状态，而不是显示空白页面。"""
        source = self.contract.segment(self.contract.catalog)
        self.assertIn("empty_widget", source)
        self.assertRegex(source, r"setVisible\([^\n]*(?:visible_count|not\s+\w+|==\s*0)")

    def test_tool_catalog_search_includes_description_and_category(self):
        """搜索应覆盖命令/插件之外的描述和分类字段。"""
        filter_method = self.contract.method("_filter_cards")
        query_memberships: list[str] = []
        for node in ast.walk(filter_method):
            if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
                continue
            if node.left.id != "query" or not any(isinstance(op, ast.In) for op in node.ops):
                continue
            for comparator in node.comparators:
                query_memberships.append(self.contract.segment(comparator).lower())

        self.assertTrue(
            any(re.search(r"description|cmd_desc|desc", expression) for expression in query_memberships),
            "_filter_cards 应将 query 与命令/插件描述进行匹配",
        )
        self.assertTrue(
            any("category" in expression for expression in query_memberships),
            "_filter_cards 应将 query 与工具分类进行匹配",
        )

    def test_tool_catalog_has_refresh_entry(self):
        """目录应提供用户可触发的刷新入口，并连接到 reload_tools。"""
        init_ui = self.contract.method("_init_ui")
        calls = self.contract.call_nodes(init_ui)
        has_refresh_button = any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "QPushButton"
            and any(
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                and "刷新" in argument.value
                for argument in call.args
            )
            for call in calls
        )
        has_reload_connection = any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "connect"
            and call.args
            and isinstance(call.args[0], ast.Attribute)
            and call.args[0].attr == "reload_tools"
            for call in calls
        )
        self.assertTrue(has_refresh_button, "ToolCatalogView 应提供刷新按钮或等价刷新入口")
        self.assertTrue(has_reload_connection, "刷新入口应连接 ToolCatalogView.reload_tools")

    def test_tool_catalog_reads_commands_from_runtime(self):
        """工具目录必须通过 Runtime.list_commands 建立命令索引。"""
        has_list_commands_call = any(
            isinstance(call.func, ast.Attribute) and call.func.attr == "list_commands"
            for call in self.contract.call_nodes(self.contract.catalog)
        )
        self.assertTrue(has_list_commands_call, "ToolCatalogView 必须调用 Runtime.list_commands()")

    def test_gui_does_not_open_sqlite_directly(self):
        """GUI 只能通过 Runtime 访问任务/插件数据，不得自行连接 SQLite。"""
        sqlite_imports = [
            node
            for node in ast.walk(self.contract.tree)
            if isinstance(node, ast.Import)
            and any(alias.name == "sqlite3" for alias in node.names)
        ]
        sqlite_from_imports = [
            node
            for node in ast.walk(self.contract.tree)
            if isinstance(node, ast.ImportFrom) and node.module == "sqlite3"
        ]
        direct_sqlite_calls = [
            node
            for node in ast.walk(self.contract.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"sqlite3", "QSqlDatabase"}
        ]
        self.assertFalse(sqlite_imports, "GUI 不应 import sqlite3")
        self.assertFalse(sqlite_from_imports, "GUI 不应从 sqlite3 导入连接 API")
        self.assertFalse(direct_sqlite_calls, "GUI 不应直接调用 SQLite connect")
        self.assertNotRegex(self.contract.source, r"QSqlDatabase\s*\.\s*addDatabase")


if __name__ == "__main__":
    unittest.main()
