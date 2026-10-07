from __future__ import annotations

import copy
import json
import math
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch
import unittest

from testbox.core.schema_validator import SchemaValidationError, SchemaValidator
from testbox.core.runtime import Runtime


class SchemaValidatorSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = SchemaValidator()

    def test_nested_defaults_do_not_mutate_caller_or_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "options": {
                    "type": "object",
                    "default": {"tags": []},
                    "properties": {
                        "value": {"type": "string", "default": "default"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                }
            },
        }
        original_schema = copy.deepcopy(schema)
        params = {"options": {}}
        result = self.validator.validate(schema, params)
        self.assertEqual(params, {"options": {}})
        # An explicitly supplied object receives its property defaults, not
        # a merge with the object's default (which only applies when absent).
        self.assertEqual(result["options"], {"value": "default"})
        result["options"]["value"] = "runtime-only"
        defaulted = self.validator.validate(schema, {})
        self.assertEqual(defaulted["options"], {"tags": [], "value": "default"})
        defaulted["options"]["tags"].append("runtime-only")
        defaulted["options"]["value"] = "runtime-only"
        self.assertEqual(schema, original_schema)
        self.assertEqual(self.validator.validate(schema, {})["options"], {"tags": [], "value": "default"})

    def test_enum_rejects_none_when_none_is_not_declared(self):
        with self.assertRaises(SchemaValidationError):
            self.validator.validate(
                {"type": "object", "properties": {"value": {"enum": ["yes"]}}},
                {"value": None},
            )

    def test_enum_can_explicitly_allow_none(self):
        result = self.validator.validate(
            {"type": "object", "properties": {"value": {"type": ["string", "null"], "enum": [None, "yes"]}}},
            {"value": None},
        )
        self.assertIsNone(result["value"])

    def test_non_finite_numbers_are_rejected(self):
        schema = {"type": "object", "properties": {"value": {"type": "number"}}}
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(SchemaValidationError):
                self.validator.validate(schema, {"value": value})


    def test_array_defaults_and_input_items_have_independent_nested_values(self):
        schema = {"type": "object", "properties": {
            "rows": {"type": "array", "default": [{}, {}], "items": {
                "type": "object", "properties": {
                    "tags": {"type": "array", "default": [], "items": {"type": "string"}},
                },
            }},
        }}
        before = copy.deepcopy(schema)
        params = {"rows": [{}, {"tags": ["existing"]}]}
        params_before = copy.deepcopy(params)
        result = self.validator.validate(schema, params)
        result["rows"][0]["tags"].append("first")
        result["rows"][1]["tags"].append("second")
        self.assertEqual(params, params_before)
        first = self.validator.validate(schema, {})
        first["rows"][0]["tags"].append("local")
        self.assertEqual(first["rows"][1]["tags"], [])
        self.assertEqual(self.validator.validate(schema, {})["rows"], [{"tags": []}, {"tags": []}])
        self.assertEqual(schema, before)

    def test_failed_validation_does_not_mutate_nested_input(self):
        schema = {"type": "object", "properties": {
            "options": {"type": "object", "required": ["required_value"], "properties": {
                "tags": {"type": "array", "default": [], "items": {"type": "string"}},
                "required_value": {"type": "string"},
            }},
        }}
        params = {"options": {}}
        before = copy.deepcopy(schema)
        with self.assertRaises(SchemaValidationError) as caught:
            self.validator.validate(schema, params)
        self.assertEqual(caught.exception.field, "required_value")
        self.assertEqual(params, {"options": {}})
        self.assertEqual(schema, before)

    def test_nullable_objects_and_arrays_apply_defaults_only_when_present(self):
        schema = {"type": "object", "properties": {
            "options": {"type": ["object", "null"], "properties": {
                "name": {"type": "string", "default": "default"},
            }},
            "rows": {"type": ["array", "null"], "items": {
                "type": "object", "properties": {"name": {"type": "string", "default": "row"}},
            }},
        }}
        params = {"options": {}, "rows": [{}]}
        self.assertEqual(self.validator.validate(schema, params),
                         {"options": {"name": "default"}, "rows": [{"name": "row"}]})
        self.assertEqual(params, {"options": {}, "rows": [{}]})
        self.assertEqual(self.validator.validate(schema, {"options": None, "rows": None}),
                         {"options": None, "rows": None})

    def test_open_value_errors_keep_top_level_field_mapping_for_gui(self):
        schema = {"type": "object", "properties": {
            "fields": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        }}
        with self.assertRaises(SchemaValidationError) as caught:
            self.validator.validate(schema, {"fields": [{"options": {"weight": math.inf}}]})
        self.assertEqual(caught.exception.field, "fields[0].options.weight")
        self.assertEqual(caught.exception.code, "INVALID_PARAMETER")

    def test_union_types_check_non_null_values_and_keep_numeric_bool_distinction(self):
        schema = {"type": "object", "properties": {"value": {"type": ["integer", "null"]}}}
        for value in (None, 0, 12):
            with self.subTest(value=value):
                self.assertEqual(self.validator.validate(schema, {"value": value}), {"value": value})
        for value in (True, False, 1.5, "12", [], {}):
            with self.subTest(value=value), self.assertRaises(SchemaValidationError) as caught:
                self.validator.validate(schema, {"value": value})
            self.assertEqual(caught.exception.field, "value")

    def test_invalid_defaults_are_validated_without_changing_the_definition(self):
        for definition in ({"type": "integer", "default": True},
                           {"type": "number", "default": math.inf},
                           {"type": "string", "enum": ["allowed"], "default": None}):
            with self.subTest(definition=definition):
                schema = {"type": "object", "properties": {"value": definition}}
                before = copy.deepcopy(schema)
                with self.assertRaises(SchemaValidationError):
                    self.validator.validate(schema, {})
                self.assertEqual(schema, before)

    def test_runtime_host_and_history_use_isolated_defaults_across_repeated_runs(self):
        schema = {"type": "object", "properties": {
            "options": {"type": "object", "default": {}, "properties": {
                "label": {"type": "string", "default": "TEST DATA ONLY"},
                "tags": {"type": "array", "default": [], "items": {"type": "string"}},
            }},
        }}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plugin = root / "plugins" / "default-fixture"
            (plugin / "schemas").mkdir(parents=True)
            (plugin / "src").mkdir()
            (plugin / "manifest.yaml").write_text("""schema_version: 1
name: default-fixture
version: 1.0.0
description: TEST DATA ONLY default regression
category: test
core_compatibility: ">=1.0.0,<2.0.0"
entry: src.main:Plugin
commands:
  - name: defaults.check
    description: Test default isolation
    input_schema: schemas/run.json
capabilities:
  concurrency: true
  network: false
  filesystem: output-only
  resources: []
""", encoding="utf-8")
            (plugin / "schemas" / "run.json").write_text(json.dumps(schema), encoding="utf-8")
            (plugin / "src" / "main.py").write_text("""from testbox.sdk import Result
class Plugin:
    def init(self, context): self.context = context
    def execute(self, command, params):
        params['options']['tags'].append('HOST ONLY')
        return Result('success', 'TEST DATA ONLY', data=params)
    def destroy(self): pass
""", encoding="utf-8")
            runtime = Runtime(root)
            try:
                supplied = {"options": {}}
                first_id, first = runtime.run("defaults.check", supplied)
                second_id, second = runtime.run("defaults.check", {})
                self.assertEqual(supplied, {"options": {}})
                expected = {"options": {"label": "TEST DATA ONLY", "tags": ["HOST ONLY"]}}
                self.assertEqual(first.status, "success")
                self.assertEqual(second.status, "success")
                self.assertEqual(first.data, expected)
                self.assertEqual(second.data, expected)
                for task_id in (first_id, second_id):
                    self.assertEqual(runtime.get_task(task_id)["status"], "SUCCEEDED")
                    manifest = json.loads((runtime.workspace_dir / task_id / "manifest.json").read_text())
                    self.assertEqual(manifest["params"], {"options": {"label": "TEST DATA ONLY", "tags": []}})
                self.assertEqual(runtime.get_command_schema("defaults.check"), schema)
            finally:
                runtime.close()

    def test_official_schema_nested_non_finite_value_is_rejected_before_task_or_host(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(source / "plugins", root / "plugins", ignore=shutil.ignore_patterns("__pycache__"))
            runtime = Runtime(root)
            try:
                before = set(runtime.workspace_dir.iterdir())
                with patch.object(runtime.process_runner, "run") as host:
                    with self.assertRaises(SchemaValidationError) as caught:
                        runtime.run("data.mock", {"count": 1, "format": "json", "fields": [
                            {"name": "value", "nullable_rate": math.nan},
                        ]})
                self.assertEqual(caught.exception.field, "nullable_rate")
                host.assert_not_called()
                self.assertEqual(runtime.count_tasks(), 0)
                self.assertEqual(set(runtime.workspace_dir.iterdir()), before)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
