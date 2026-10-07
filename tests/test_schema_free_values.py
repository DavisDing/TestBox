"""Non-finite parameter regressions, including schema-free plugin extensions.

Only parameter trees are covered: plugin config, file contents and cycles are
outside this contract. CLI/Host integration uses official plugins in a temporary
root and an isolated HOME, never the checkout's runtime workspace.
"""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from testbox.core.runtime import Runtime
from testbox.core.schema_validator import SchemaValidationError, SchemaValidator


ROOT = Path(__file__).resolve().parents[1]
NONFINITE = (math.nan, math.inf, -math.inf)
SECRET = "SYNTHETIC_FREE_VALUE_SECRET_NOT_REAL"
FINITE_VALUE = {
    "numbers": [0, -13, 0.25, -0.0, 5e-324, 1.7976931348623157e308, 10**100],
    "other": [None, True, False, "NaN", "Infinity", "-Infinity", "1e999"],
    "nested": [{"empty_list": [], "empty_object": {}, "label": "TEST DATA ONLY"}],
}


def snapshot(value: object) -> str:
    # JSON snapshots compare NaN without relying on NaN's unusual equality.
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def official_params(surface: str, value: object) -> dict:
    field = {"name": "value", "generator": "constant", "options": {"value": 1}}
    if surface == "options":
        field["options"]["extension"] = value
    elif surface == "default":
        field["default"] = value
    elif surface in ("field_extension", "rules"):
        field["extension"] = value
    else:
        raise AssertionError(f"Unexpected fixture surface: {surface}")
    return {"count": 1, "format": "json", "rules" if surface == "rules" else "fields": [field]}


class FreeValueAssertions:
    def assert_safe_validation_error(self, error: SchemaValidationError) -> None:
        self.assertEqual(error.code, "INVALID_PARAMETER")
        self.assertTrue(error.field)
        self.assert_diagnostic_has_no_values(str(error))

    def assert_diagnostic_has_no_values(self, text: str) -> None:
        self.assertNotIn(SECRET, text)
        for raw_number in ("nan", "infinity", "-inf", "1e999"):
            self.assertNotIn(raw_number, text.lower())
        # Python's float repr is inf, not Infinity.
        self.assertNotRegex(text.lower(), r"\binf\b")


class SchemaFreeValueTests(FreeValueAssertions, unittest.TestCase):
    def setUp(self) -> None:
        self.validator = SchemaValidator()

    def assert_rejected_without_mutation(self, schema: dict, params: dict) -> None:
        schema_before, params_before = snapshot(schema), snapshot(params)
        with self.assertRaises(SchemaValidationError) as caught:
            self.validator.validate(schema, params)
        self.assert_safe_validation_error(caught.exception)
        self.assertEqual(snapshot(schema), schema_before)
        self.assertEqual(snapshot(params), params_before)

    def test_open_root_rejects_nonfinite_scalar_parameters(self):
        schema = {"type": "object", "additionalProperties": True}
        for value in NONFINITE:
            with self.subTest(value=value):
                self.assert_rejected_without_mutation(schema, {"api_key": value, "password": SECRET})

    def test_open_nested_objects_reject_nonfinite_inside_lists_and_dicts(self):
        schema = {"type": "object", "properties": {
            "free": {"type": "object", "additionalProperties": True},
        }}
        for value in NONFINITE:
            with self.subTest(value=value):
                self.assert_rejected_without_mutation(schema, {
                    "free": {"nested": [[{"api_key": value, "password": SECRET}]]},
                })

    def test_arrays_without_items_schema_reject_nested_nonfinite(self):
        schema = {"type": "object", "properties": {"free": {"type": "array"}}}
        for value in NONFINITE:
            with self.subTest(value=value):
                self.assert_rejected_without_mutation(schema, {"free": [0, {"nested": [value]}]})

    def test_arrays_with_empty_items_schema_reject_nested_nonfinite(self):
        schema = {"type": "object", "properties": {
            "free": {"type": "array", "items": {}},
        }}
        for value in NONFINITE:
            with self.subTest(value=value):
                # The outer item is a list so existing object-key constraints
                # do not reject it before the free-tree finite scan is exercised.
                self.assert_rejected_without_mutation(schema, {"free": [[{"nested": [value]}]]})

    def test_empty_property_schema_rejects_scalar_and_nested_nonfinite(self):
        schema = {"type": "object", "properties": {"free": {}}}
        for value in NONFINITE:
            for supplied in (value, [{"nested": [value]}]):
                with self.subTest(value=value, nested=isinstance(supplied, list)):
                    self.assert_rejected_without_mutation(schema, {"free": supplied})

    def test_open_schema_without_type_rejects_nested_nonfinite(self):
        schema = {"additionalProperties": True}
        for value in NONFINITE:
            with self.subTest(value=value):
                self.assert_rejected_without_mutation(schema, {"free": [{"nested": [value]}]})

    def test_default_arrays_without_items_schema_reject_nonfinite(self):
        for value in NONFINITE:
            with self.subTest(value=value):
                schema = {"type": "object", "properties": {
                    "free": {"type": "array", "default": [{"nested": [value]}]},
                }}
                self.assert_rejected_without_mutation(schema, {})

    def test_default_arrays_with_empty_items_schema_reject_nonfinite(self):
        for value in NONFINITE:
            with self.subTest(value=value):
                schema = {"type": "object", "properties": {
                    "free": {"type": "array", "items": {}, "default": [[{"nested": [value]}]]},
                }}
                self.assert_rejected_without_mutation(schema, {})

    def test_nested_item_defaults_reject_nonfinite_without_mutating_inputs(self):
        for value in NONFINITE:
            with self.subTest(value=value):
                schema = {"type": "object", "properties": {
                    "rows": {"type": "array", "items": {"type": "object", "properties": {
                        "free": {"default": [{"nested": [value]}]},
                    }}},
                }}
                self.assert_rejected_without_mutation(schema, {"rows": [{}, {}]})

    def test_finite_open_values_remain_allowed_and_are_deeply_isolated(self):
        schemas_and_params = [
            ({"type": "object", "additionalProperties": True}, {"free": copy.deepcopy(FINITE_VALUE)}),
            ({"additionalProperties": True}, {"free": copy.deepcopy(FINITE_VALUE)}),
            ({"type": "object", "properties": {"free": {"type": "array"}}},
             {"free": [copy.deepcopy(FINITE_VALUE)]}),
            ({"type": "object", "properties": {"free": {"type": "array", "items": {}}}},
             {"free": [[copy.deepcopy(FINITE_VALUE)]]}),
            ({"type": "object", "properties": {"free": {}}},
             {"free": [copy.deepcopy(FINITE_VALUE)]}),
        ]
        for schema, params in schemas_and_params:
            with self.subTest(schema=schema):
                schema_before, params_before = snapshot(schema), snapshot(params)
                result = self.validator.validate(schema, params)
                self.assertEqual(result, params)
                self.assertIsNot(result["free"], params["free"])
                result["free"].clear()
                self.assertEqual(snapshot(params), params_before)
                self.assertEqual(snapshot(schema), schema_before)
        schema = {"type": "object", "properties": {"free": {}}}
        for value in (None, True, False, "NaN", "Infinity", "1e999", 0, 1.5, [], {}):
            with self.subTest(empty_schema_value=value):
                self.assertEqual(self.validator.validate(schema, {"free": value}), {"free": value})

    def test_finite_default_arrays_are_allowed_and_do_not_share_schema_state(self):
        for definition in ({"type": "array"}, {"type": "array", "items": {}}, {}):
            with self.subTest(definition=definition):
                definition = {**definition, "default": [[copy.deepcopy(FINITE_VALUE)]]}
                schema = {"type": "object", "properties": {"free": definition}}
                schema_before = snapshot(schema)
                params = {}
                first = self.validator.validate(schema, params)
                self.assertEqual(first["free"], definition["default"])
                first["free"][0][0]["numbers"].append(99)
                second = self.validator.validate(schema, params)
                self.assertEqual(second["free"], definition["default"])
                self.assertEqual(params, {})
                self.assertEqual(snapshot(schema), schema_before)

    def test_shared_finite_aliases_are_allowed_without_touching_caller_values(self):
        shared = {"nested": [copy.deepcopy(FINITE_VALUE)]}
        params = {"left": shared, "right": shared, "also": [shared]}
        schema = {"type": "object", "additionalProperties": True}
        before = snapshot(params)
        result = self.validator.validate(schema, params)
        self.assertEqual(result, params)
        self.assertIs(params["left"], params["right"])
        self.assertIs(params["left"], params["also"][0])
        self.assertIsNot(result["left"], shared)
        result["left"]["nested"].clear()
        self.assertEqual(snapshot(params), before)


class OfficialFreeValueTests(FreeValueAssertions, unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="testbox-free-values-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        shutil.copytree(ROOT / "plugins", self.root / "plugins",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.environment = {
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "XDG_DATA_HOME": str(self.home / "data"),
            "LOCALAPPDATA": str(self.home / "local"),
            "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        # Keep installed Python's environment but exclude user plugin overrides.
        clean_environment = {key: value for key, value in os.environ.items()
                             if not key.startswith("TESTBOX_")}
        clean_environment.update(self.environment)
        self.environment = clean_environment
        environment_patch = patch.dict(os.environ, self.environment, clear=True)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)
        self.runtime = Runtime(self.root, timeout_seconds=20)
        self.addCleanup(self.runtime.close)
        self.assertIn("data.mock", self.runtime.list_commands())
        self.schema_before = snapshot(self.runtime.get_command_schema("data.mock"))

    def assert_runtime_rejects_without_task(self, params: dict) -> None:
        params_before = snapshot(params)
        workspace_before = set(self.runtime.workspace_dir.rglob("*"))
        with patch.object(self.runtime.workspace, "create") as create_workspace, \
                patch.object(self.runtime.history, "create") as create_history, \
                patch.object(self.runtime.process_runner, "run") as host:
            with self.assertRaises(SchemaValidationError) as caught:
                self.runtime.run("data.mock", params)
            self.assert_safe_validation_error(caught.exception)
            create_workspace.assert_not_called()
            create_history.assert_not_called()
            host.assert_not_called()
        self.assertEqual(self.runtime.count_tasks(), 0)
        self.assertEqual(self.runtime.list_tasks(), [])
        self.assertEqual(set(self.runtime.workspace_dir.rglob("*")), workspace_before)
        self.assertEqual(snapshot(params), params_before)
        self.assertEqual(snapshot(self.runtime.get_command_schema("data.mock")), self.schema_before)

    def test_options_nonfinite_rejected_before_workspace_history_or_host(self):
        for value in NONFINITE:
            for extension in (value, [{"api_key": value, "password": SECRET}]):
                with self.subTest(value=value, nested=isinstance(extension, list)):
                    self.assert_runtime_rejects_without_task(official_params("options", extension))

    def test_rules_nonfinite_rejected_before_workspace_history_or_host(self):
        for value in NONFINITE:
            with self.subTest(value=value):
                self.assert_runtime_rejects_without_task(official_params(
                    "rules", {"nested": [{"api_key": value, "password": SECRET}]},
                ))

    def test_field_default_nonfinite_rejected_before_workspace_history_or_host(self):
        for value in NONFINITE:
            for default in (value, [{"api_key": value, "password": SECRET}]):
                with self.subTest(value=value, nested=isinstance(default, list)):
                    self.assert_runtime_rejects_without_task(official_params("default", default))

    def test_field_additional_property_nonfinite_rejected_before_task_or_host(self):
        for value in NONFINITE:
            with self.subTest(value=value):
                self.assert_runtime_rejects_without_task(official_params(
                    "field_extension", [{"api_key": value, "password": SECRET}],
                ))

    def cli(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "testbox.cli", "--json", *arguments],
            cwd=self.root, env=self.environment, capture_output=True,
            text=True, encoding="utf-8", timeout=30,
        )

    def test_real_cli_rejects_set_and_params_file_nonfinite_without_task_or_leaks(self):
        placeholder = "NONFINITE_LITERAL_PLACEHOLDER"
        workspace_before = set(self.runtime.workspace_dir.rglob("*"))
        for transport in ("set", "params_file"):
            for surface in ("options", "rules", "default"):
                for literal in ("NaN", "Infinity", "-Infinity", "1e999"):
                    with self.subTest(transport=transport, surface=surface, literal=literal):
                        params = official_params(surface, {
                            "nested": [{"api_key": placeholder, "password": SECRET}],
                        })
                        serialized = snapshot(params).replace(json.dumps(placeholder), literal)
                        if transport == "params_file":
                            params_file = self.root / "params.json"
                            params_file.write_text(serialized, encoding="utf-8")
                            process = self.cli("run", "data.mock", "--params-file", str(params_file))
                        else:
                            field_key = "rules" if surface == "rules" else "fields"
                            fields = snapshot(params[field_key]).replace(json.dumps(placeholder), literal)
                            process = self.cli("run", "data.mock", "--set", "count=1",
                                               "--set", "format=json", "--set", f"{field_key}={fields}")
                        self.assertEqual(process.returncode, 2, process.stderr)
                        self.assertEqual(process.stdout.strip(), "")
                        error = json.loads(process.stderr)
                        self.assertIs(error["ok"], False)
                        # CLI's stable vocabulary is INVALID_PARAMS; direct
                        # SchemaValidationError uses INVALID_PARAMETER above.
                        self.assertEqual(error["error"]["code"], "INVALID_PARAMS")
                        self.assertTrue(error["error"]["message"])
                        self.assertNotIn("task_id", error)
                        self.assertNotIn("task_id", error["error"])
                        self.assert_diagnostic_has_no_values(process.stdout + process.stderr)
                        self.assertEqual(self.runtime.count_tasks(), 0)
                        self.assertEqual(self.runtime.list_tasks(), [])
                        self.assertEqual(set(self.runtime.workspace_dir.rglob("*")), workspace_before)
        self.assertEqual(snapshot(self.runtime.get_command_schema("data.mock")), self.schema_before)

    def assert_constant_output(self, task_id: str, files: list[str]) -> None:
        self.assertEqual(self.runtime.get_task(task_id)["status"], "SUCCEEDED")
        self.assertEqual(self.runtime.get_task_result(task_id)["status"], "success")
        self.assertEqual(len(files), 1)
        path = self.runtime.get_task_output_path(task_id, files[0])
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), [{"value": FINITE_VALUE}] * 2)

    def test_finite_free_constant_runs_real_host_and_writes_exact_json(self):
        for field_key in ("fields", "rules"):
            with self.subTest(field_key=field_key):
                params = {"count": 2, "format": "json", field_key: [{
                    "name": "value", "generator": "constant",
                    "options": {"value": copy.deepcopy(FINITE_VALUE)},
                    "default": [copy.deepcopy(FINITE_VALUE)],
                    "extension": copy.deepcopy(FINITE_VALUE),
                }]}
                params_before = snapshot(params)
                task_id, result = self.runtime.run("data.mock", params)
                self.assertEqual(result.status, "success", result.to_dict())
                self.assert_constant_output(task_id, result.files)
                self.assertEqual(snapshot(params), params_before)
        self.assertEqual(snapshot(self.runtime.get_command_schema("data.mock")), self.schema_before)

    def test_real_cli_finite_free_constant_runs_host_and_writes_exact_json(self):
        params = {"count": 2, "format": "json", "fields": [{
            "name": "value", "generator": "constant", "options": {"value": FINITE_VALUE},
        }]}
        params_file = self.root / "finite-params.json"
        params_file.write_text(json.dumps(params, allow_nan=False), encoding="utf-8")
        before = params_file.read_bytes()
        process = self.cli("run", "data.mock", "--params-file", str(params_file))
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr.strip(), "")
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "success")
        self.assert_constant_output(result["task_id"], result["files"])
        self.assertEqual(params_file.read_bytes(), before)
        self.assertEqual(self.runtime.count_tasks(), 1)
        self.assertEqual(snapshot(self.runtime.get_command_schema("data.mock")), self.schema_before)


if __name__ == "__main__":
    unittest.main()
