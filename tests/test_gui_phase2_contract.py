"""GUI Phase 2 contracts that do not require the optional PySide6 package."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from testbox.core.runtime import Runtime
from testbox.core.schema_validator import SchemaValidationError, SchemaValidator


ROOT = Path(__file__).resolve().parents[1]
GUI_PATH = ROOT / "testbox" / "gui.py"


class GuiPhase2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = GUI_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(GUI_PATH))

    def _class(self, name: str) -> ast.ClassDef:
        return next(
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.ClassDef) and node.name == name
        )

    def _method(self, class_name: str, method_name: str) -> ast.FunctionDef:
        owner = self._class(class_name)
        return next(
            node for node in owner.body
            if isinstance(node, ast.FunctionDef) and node.name == method_name
        )

    def _segment(self, node: ast.AST) -> str:
        value = ast.get_source_segment(self.source, node)
        self.assertIsNotNone(value)
        return value or ""

    def test_submit_uses_runtime_parameter_validation(self):
        source = self._segment(self._method("CommandDetailFormView", "_on_submit"))
        self.assertIn("self.runtime.validate_params", source)
        self.assertIn("SchemaValidationError", source)

    def test_schema_errors_can_be_mapped_to_field_feedback(self):
        source = self._segment(self._method("DynamicSchemaForm", "set_field_error"))
        self.assertIn("error_labels", source)
        self.assertIn("advanced_keys", source)
        self.assertIn("setChecked(True)", source)

    def test_local_validation_checks_file_inputs(self):
        source = self._segment(self._method("DynamicSchemaForm", "validate_locally"))
        self.assertIn('field_type == "file-path"', source)
        self.assertIn('field_type == "array-files"', source)
        self.assertIn("path.exists()", source)
        self.assertIn("path.is_file()", source)

    def test_invalid_json_is_not_silently_submitted(self):
        source = self._segment(self._method("DynamicSchemaForm", "get_values"))
        self.assertIn("FormInputError", source)
        self.assertIn("json.JSONDecodeError", source)

    def test_runtime_validate_params_applies_defaults_and_reports_field(self):
        runtime = Runtime.__new__(Runtime)
        runtime.schema_validator = SchemaValidator()
        runtime.get_command_schema = lambda command: {
            "type": "object",
            "required": ["input"],
            "properties": {
                "input": {"type": "string"},
                "format": {"type": "string", "default": "json"},
            },
            "additionalProperties": False,
        }

        self.assertEqual(
            runtime.validate_params("sample.run", {"input": "data.txt"}),
            {"input": "data.txt", "format": "json"},
        )
        with self.assertRaises(SchemaValidationError) as caught:
            runtime.validate_params("sample.run", {})
        self.assertEqual(caught.exception.field, "input")


if __name__ == "__main__":
    unittest.main()
