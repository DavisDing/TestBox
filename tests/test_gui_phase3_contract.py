"""Static GUI Phase 3 contracts.

The suite inspects :mod:`testbox.gui` without importing it so it remains runnable
when the optional PySide6 dependency is not installed.  These contracts protect
the Runtime-owned task state, result lookup, and export boundaries described in
``docs/proposals/gui-information-architecture.md``.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI_PATH = ROOT / "testbox" / "gui.py"
RUNTIME_STATUSES = {
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "ABANDONED",
}
DISPLAY_FACETS = {"WARNING", "EMPTY"}


class GuiPhase3Source:
    """AST/source adapter that never imports the optional Qt GUI module."""

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
            for call in GuiPhase3Source.calls(node)
            if isinstance(call.func, ast.Attribute)
        ]

    @staticmethod
    def compared_status_literals(node: ast.AST) -> set[str]:
        """Return uppercase literals directly compared with a ``status`` value."""
        values: set[str] = set()
        for item in ast.walk(node):
            if not isinstance(item, ast.Compare):
                continue
            operands = [item.left, *item.comparators]
            has_status_operand = any(
                isinstance(operand, ast.Name) and operand.id == "status"
                for operand in operands
            )
            if not has_status_operand:
                continue
            values.update(
                operand.value
                for operand in operands
                if isinstance(operand, ast.Constant)
                and isinstance(operand.value, str)
                and operand.value.isupper()
            )
        return values


class GuiPhase3ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gui = GuiPhase3Source()

    def test_task_detail_maps_all_six_runtime_statuses(self):
        """The detail banner must explicitly render every Runtime task state."""
        render = self.gui.method("TaskResultDetailView", "_render_status_banner")
        mapped = self.gui.compared_status_literals(render)
        self.assertEqual(
            mapped & RUNTIME_STATUSES,
            RUNTIME_STATUSES,
            "任务详情必须显式映射 PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED/ABANDONED",
        )

    def test_warning_and_empty_are_not_runtime_status_branches(self):
        """Warning/empty are result facets and must not become task-state branches."""
        render = self.gui.method("TaskResultDetailView", "_render_status_banner")
        compared = self.gui.compared_status_literals(render)
        self.assertFalse(
            compared & DISPLAY_FACETS,
            "WARNING/EMPTY 只能作为展示层 facet，不能作为 Runtime status 分支",
        )

    def test_task_detail_reads_task_and_result_through_runtime(self):
        """Task metadata and result data must continue to come from Runtime APIs."""
        display = self.gui.method("TaskResultDetailView", "display_task")
        calls = self.gui.calls(display)
        runtime_calls = {
            call.func.attr
            for call in calls
            if isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Attribute)
            and isinstance(call.func.value.value, ast.Name)
            and call.func.value.value.id == "self"
            and call.func.value.attr == "runtime"
        }
        self.assertIn("get_task", runtime_calls)
        self.assertIn("get_task_result", runtime_calls)

    def test_exports_use_only_runtime_commit_apis(self):
        """Single and archive exports must delegate to Runtime; GUI must not copy."""
        export_one = self.gui.method("TaskResultDetailView", "_export_single_file")
        export_all = self.gui.method("TaskResultDetailView", "_export_all_as_zip")
        self.assertIn("commit_output", self.gui.attribute_call_names(export_one))
        self.assertIn("commit_outputs_archive", self.gui.attribute_call_names(export_all))

        forbidden_imports = [
            node
            for node in ast.walk(self.gui.tree)
            if (
                isinstance(node, ast.Import)
                and any(alias.name == "shutil" for alias in node.names)
            )
            or (isinstance(node, ast.ImportFrom) and node.module == "shutil")
        ]
        forbidden_copy_calls = [
            call
            for call in self.gui.calls(self.gui.tree)
            if isinstance(call.func, ast.Attribute)
            and call.func.attr in {"copy", "copy2", "copyfile", "copytree", "move"}
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "shutil"
        ]
        self.assertFalse(forbidden_imports, "GUI 不应导入 shutil 绕过 Runtime 导出")
        self.assertFalse(forbidden_copy_calls, "GUI 不应使用 shutil 直接复制任务产物")

    def test_running_view_uses_indeterminate_progress_without_fake_percentage(self):
        """The synchronous Host protocol only supports an indeterminate indicator."""
        running = self.gui.class_node("RunningWorkspaceView")
        source = self.gui.segment(running)
        calls = self.gui.calls(running)
        has_indeterminate_range = any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "setRange"
            and len(call.args) >= 2
            and all(
                isinstance(arg, ast.Constant) and arg.value == 0
                for arg in call.args[:2]
            )
            for call in calls
        )
        self.assertTrue(has_indeterminate_range, "运行页应使用 QProgressBar.setRange(0, 0)")
        self.assertNotRegex(
            source,
            r"(?<![A-Za-z0-9_])(?:[1-9]\d?|100)\s*%",
            "运行页不得展示没有 Runtime/Host 数据支撑的伪百分比",
        )

    def test_gui_does_not_call_unsupported_cancellation_protocol(self):
        """Phase 3 must not invent cancellation while Host remains request/response."""
        forbidden = {
            "cancel_task",
            "cancel_run",
            "request_cancel",
            "abort_task",
            "terminate",
            "kill",
        }
        called = set(self.gui.attribute_call_names(self.gui.tree))
        self.assertFalse(
            called & forbidden,
            f"GUI 不得调用尚未支持的取消协议: {sorted(called & forbidden)}",
        )
        self.assertNotRegex(
            self.gui.source,
            r"\b(?:cancelRequested|cancelTaskRequested|taskCancelRequested)\b",
            "GUI 不应声明尚无 Runtime/Host 支持的任务取消信号",
        )

    def test_pending_and_running_have_user_visible_detail_states(self):
        """Opening an in-flight task must show meaningful PENDING/RUNNING details."""
        render = self.gui.method("TaskResultDetailView", "_render_status_banner")
        source = self.gui.segment(render)
        compared = self.gui.compared_status_literals(render)
        self.assertTrue({"PENDING", "RUNNING"}.issubset(compared))
        self.assertRegex(source, r"等待执行|排队中|等待插件返回|PENDING")
        self.assertRegex(source, r"正在运行|执行中|等待插件返回|RUNNING")


if __name__ == "__main__":
    unittest.main()
