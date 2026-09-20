"""Static GUI Phase 5 contracts for the sql.parse -> sql.select handoff.

The GUI dependency is optional, so these tests inspect source without importing
PySide6.  They protect the Runtime-owned artifact boundary and the requirement
that prefill never authorizes automatic execution.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI_PATH = ROOT / "testbox" / "gui.py"


class GuiPhase5Source:
    def __init__(self) -> None:
        self.source = GUI_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source, filename=str(GUI_PATH))

    def method(self, class_name: str, method_name: str) -> ast.FunctionDef:
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.ClassDef) or node.name != class_name:
                continue
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == method_name:
                    return member
        raise AssertionError(f"{class_name} 未找到方法: {method_name}")

    def segment(self, node: ast.AST) -> str:
        result = ast.get_source_segment(self.source, node)
        if result is None:
            raise AssertionError(f"无法读取节点源码: {type(node).__name__}")
        return result

    @staticmethod
    def attribute_calls(node: ast.AST) -> set[str]:
        return {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        }

    @staticmethod
    def runtime_calls(node: ast.AST) -> set[str]:
        calls: set[str] = set()
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            owner = call.func.value
            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "self"
                and owner.attr == "runtime"
            ):
                calls.add(call.func.attr)
        return calls


class GuiPhase5ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gui = GuiPhase5Source()

    def test_parse_to_select_is_runtime_validated_prefill_only(self):
        handoff = self.gui.method("TaskResultDetailView", "_on_open_sql_select")
        source = self.gui.segment(handoff)
        self.assertIn("get_task_output_path", self.gui.runtime_calls(handoff))
        self.assertIn("emit", self.gui.attribute_calls(handoff))
        self.assertIn('"sql.select"', source)
        self.assertIn('"input_format"', source)
        self.assertNotIn("workspace_path", source)
        self.assertFalse(
            self.gui.attribute_calls(handoff) & {"run", "execute", "execute_task", "start_running"},
            "下游快捷入口只能预填工作台，不能自动创建或执行任务",
        )

    def test_handoff_is_limited_to_successful_sql_parse_artifacts(self):
        eligibility = self.gui.method("TaskResultDetailView", "_can_open_sql_select")
        source = self.gui.segment(eligibility)
        self.assertIn('"sql.parse"', source)
        self.assertIn('"SUCCEEDED"', source)
        self.assertIn('"success"', source)
        for extension in (".json", ".csv", ".xlsx"):
            self.assertIn(extension, source)

    def test_signal_navigates_to_form_without_execution(self):
        init_ui = self.gui.method("MainWindow", "_init_ui")
        source = self.gui.segment(init_ui)
        self.assertIn("sqlSelectRequested.connect(self.navigate_to_command)", source)
        self.assertNotIn("sqlSelectRequested.connect(self.execute_task)", source)


if __name__ == "__main__":
    unittest.main()
