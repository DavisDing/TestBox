"""Data Compare contracts: public SDK unit tests and real Runtime/Host tasks."""
from __future__ import annotations

import copy
import csv
import importlib.util
import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from testbox.core.runtime import Runtime
from testbox.core.schema_validator import SchemaValidationError
from testbox.sdk import PluginError

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "data-compare"
spec = importlib.util.spec_from_file_location("data_compare_test_plugin", PLUGIN / "src" / "main.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

try:
    import openpyxl
except ImportError:
    openpyxl = None


def dataset(rows, columns=None, types=None):
    return {"columns": columns if columns is not None else list(dict.fromkeys(c for row in rows for c in row)),
            "rows": rows, "locations": [{"row": index + 1} for index in range(len(rows))],
            "format": "json", "source": "test-source.json", "warnings": [], "complete": True,
            "types": types or {}}


class ComparisonUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="testbox-compare-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8", newline="")
        return path

    def compare(self, left, right, **params):
        return module.compare(dataset(left), dataset(right), params)

    def test_strict_scalar_and_nested_identity(self):
        for left, right in [(True, 1), (False, 0), (1, 1.0), (1, "1"),
                            ({"a": True}, {"a": 1}), ([True], [1]), (None, "null")]:
            for mode in ("position", "multiset"):
                with self.subTest(left=left, right=right, mode=mode):
                    self.assertFalse(self.compare([{"v": left}], [{"v": right}], mode=mode)["equal"])
        self.assertTrue(self.compare([{"v": {"x": 1, "y": 2}}], [{"v": {"y": 2, "x": 1}}])["equal"])

    def test_absence_not_null_and_missing_marker_not_real_string(self):
        for right in (None, "<missing>"):
            report = module.compare(dataset([{}], ["v"]), dataset([{"v": right}], ["v"]), {})
            self.assertFalse(report["equal"])
            detail = report["differences"][0]
            self.assertFalse(detail["left_present"])
            self.assertTrue(detail["right_present"])
            self.assertNotIn("left", detail)

    def test_default_position_and_key_order(self):
        left = [{"id": "1", "v": "A"}, {"id": "2", "v": "B"}]
        self.assertFalse(self.compare(left, list(reversed(left)))["equal"])
        self.assertTrue(self.compare(left, list(reversed(left)), mode="key", keys=["id"])["equal"])
        self.assertTrue(self.compare(left, list(reversed(left)), mode="multiset")["equal"])

    def test_multiset_keeps_occurrence_counts_and_locations(self):
        report = self.compare([{"v": "a"}, {"v": "a"}, {"v": "b"}], [{"v": "a"}, {"v": "b"}, {"v": "b"}], mode="multiset")
        self.assertFalse(report["equal"])
        self.assertEqual((report["missing_rows"], report["added_rows"]), (1, 1))
        self.assertEqual(report["differences"][0]["left_location"]["row"], 2)

    def test_key_empty_missing_duplicate_and_whitespace_fail(self):
        for rows in ([{"id": "a"}, {"id": "a"}], [{"id": ""}], [{"id": " \t"}],
                     [{"id": None}], [{"id": "a"}, {}], [{"id": [1]}]):
            with self.subTest(rows=rows), self.assertRaises(PluginError):
                self.compare(rows, [{"id": "a"}], mode="key", keys=["id"])
        with self.assertRaises(PluginError):
            self.compare([], [], mode="key", keys=["id"])

    def test_key_type_is_not_python_equality(self):
        rows = [{"id": True}, {"id": 1}, {"id": "1"}]
        self.assertTrue(self.compare(rows, list(reversed(rows)), mode="key", keys=["id"])["equal"])

    def test_column_changes_even_with_zero_rows(self):
        report = module.compare(dataset([], ["a"]), dataset([], ["b"]), {})
        self.assertFalse(report["equal"])
        self.assertEqual(report["column_differences"], 2)
        self.assertEqual([d["kind"] for d in report["differences"]], ["missing_column", "added_column"])

    def test_column_order_is_not_semantic_and_ignore_is_explicit(self):
        report = module.compare(dataset([{"a": 1, "b": 2}], ["a", "b"]), dataset([{"b": 2, "a": 1}], ["b", "a"]), {})
        self.assertTrue(report["equal"])
        self.assertTrue(self.compare([{"id": 1, "time": "a"}], [{"id": 1}], ignore_columns=["time"])["equal"])
        with self.assertRaises(PluginError):
            self.compare([{"id": 1}], [{"id": 1}], ignore_columns=["typo"])

    def test_add_missing_and_field_changes_are_relative_to_left(self):
        report = self.compare([{"id": 1, "v": "A"}, {"id": 2, "v": "B"}],
                              [{"id": 1, "v": "changed"}, {"id": 3, "v": "C"}], mode="key", keys=["id"])
        self.assertEqual((report["changed_fields"], report["missing_rows"], report["added_rows"]), (1, 1, 1))
        self.assertEqual(report["differences"][0]["key"], {"id": 1})

    def test_truncation_does_not_short_circuit_equality_or_counts(self):
        report = self.compare([{"v": i} for i in range(2000)], [{"v": -1} for _ in range(2000)], max_differences=1)
        self.assertFalse(report["equal"])
        self.assertTrue(report["truncated"])
        self.assertEqual(report["difference_count"], 2000)
        self.assertEqual(len(report["differences"]), 1)

    def test_tolerances_for_native_numbers_not_numeric_strings_or_bool(self):
        self.assertFalse(self.compare([{"v": 1.0}], [{"v": 1.1}])["equal"])
        self.assertTrue(self.compare([{"v": 1.0}], [{"v": 1.1}], absolute_tolerance=0.1)["equal"])
        self.assertFalse(self.compare([{"v": "1"}], [{"v": "1.1"}], absolute_tolerance=1)["equal"])
        self.assertFalse(self.compare([{"v": True}], [{"v": 1}], absolute_tolerance=100)["equal"])
        self.assertTrue(self.compare([{"v": 100}], [{"v": 101}], relative_tolerance=0.01)["equal"])
        self.assertFalse(self.compare([{"v": 100}], [{"v": 102}], relative_tolerance=0.01)["equal"])
        self.assertTrue(self.compare([{"v": 0}], [{"v": 0.001}], absolute_tolerance=0.001)["equal"])

    def test_exact_decimal_beyond_decimal_context_precision(self):
        a = "1." + "0" * 40 + "1"
        b = "1." + "0" * 40 + "2"
        left = dataset([{"v": a}], types={"v": "decimal"})
        right = dataset([{"v": b}], types={"v": "decimal"})
        self.assertFalse(module.compare(left, right, {})["equal"])
        self.assertFalse(module.compare(left, right, {"absolute_tolerance": 1e-43})["equal"])
        self.assertTrue(module.compare(left, right, {"absolute_tolerance": 1e-41})["equal"])

    def test_decimal_metadata_not_treated_as_arbitrary_numeric_string(self):
        left = dataset([{"v": "1"}], types={"v": "decimal"})
        right = dataset([{"v": "1"}])
        self.assertFalse(module.compare(left, right, {})["equal"])
        self.assertFalse(module.compare(left, right, {"absolute_tolerance": 1})["equal"])

    def test_multiset_tolerance_uses_maximum_not_greedy_matching(self):
        # Greedy 1->2 prevents 0->2. An augmenting path must reassign 1->1.
        self.assertTrue(self.compare([{"v": 1}, {"v": 0}], [{"v": 2}, {"v": 1}], mode="multiset", absolute_tolerance=1)["equal"])
        self.assertTrue(self.compare([{"v": 1}, {"v": 2}], [{"v": 2}, {"v": 3}], mode="multiset", absolute_tolerance=1)["equal"])
        self.assertFalse(self.compare([{"v": 1}, {"v": 1}], [{"v": 2}], mode="multiset", absolute_tolerance=1)["equal"])

    def test_multiset_matching_exhaustive_small_oracle(self):
        pairs = list(itertools.product(range(3), repeat=3))
        for left in pairs:
            for right in pairs:
                expected = any(all(abs(a - b) <= 1 for a, b in zip(left, order)) for order in itertools.permutations(right))
                actual = self.compare([{"v": v} for v in left], [{"v": v} for v in right], mode="multiset", absolute_tolerance=1)["equal"]
                self.assertEqual(actual, expected, (left, right))

    def test_multiset_tolerance_resource_bound_is_failure_not_false_equality(self):
        with patch.object(module, "MAX_MATCH_WORK", 1), self.assertRaises(PluginError) as error:
            self.compare([{"v": 1}, {"v": 0}], [{"v": 2}, {"v": 1}], mode="multiset", absolute_tolerance=1)
        self.assertEqual(error.exception.code, "COMPARE_LIMIT_EXCEEDED")

    def test_homogeneous_multiset_tolerance_scales_without_pairwise_scan(self):
        with patch.object(module, "MAX_MATCH_WORK", 1):
            self.assertTrue(self.compare([{"v": 1}] * 1000, [{"v": 2}] * 1000, mode="multiset", absolute_tolerance=1)["equal"])

    def test_compare_never_mutates_dataset_or_parameters(self):
        left = dataset([{"id": "b", "v": {"x": 1}}, {"id": "a", "v": [2]}])
        right = copy.deepcopy(left)
        params = {"mode": "key", "keys": ["id"]}
        before = copy.deepcopy((left, right, params))
        module.compare(left, right, params)
        self.assertEqual((left, right, params), before)

    def test_business_parameter_validation(self):
        for params in ({"mode": "unknown"}, {"mode": "key"}, {"keys": ["v"]},
                       {"keys": ["v", "v"]}, {"ignore_columns": [1]},
                       {"mode": "key", "keys": ["v"], "ignore_columns": ["v"]},
                       {"absolute_tolerance": -1}, {"absolute_tolerance": True},
                       {"relative_tolerance": float("nan")}, {"absolute_tolerance": float("inf")},
                       {"max_differences": True}, {"max_differences": 0}, {"max_differences": 100001}):
            with self.subTest(params=params), self.assertRaises(PluginError):
                self.compare([{"v": 1}], [{"v": 1}], **params)

    def test_formula_csv_injection_protection_and_markdown_text(self):
        for value in ("=1+1", "+1", "-1", "@SUM(A1)", "  =evil", "\t=evil", "\r=evil", "\n=evil"):
            self.assertTrue(module._safe_csv(value).startswith("'"))
        self.assertEqual(module._safe_csv("ordinary"), "ordinary")
        self.assertNotIn("<script>", module._markdown("<script>|`\n"))

    def test_read_sdk_cross_csv_json_mapping_and_decimal(self):
        csv_path = self.write("left.csv", "ID,amount,name\n001,1.200, Alice \n")
        json_path = self.write("right.json", '[{"id":"001","amount":1.2,"name":"alice"}]')
        left = module._read(str(csv_path), {}, {"column_mapping": {"ID": "id"}, "trim": True, "casefold": True, "types": {"amount": "decimal"}})
        right = module._read(str(json_path), {}, {"types": {"amount": "decimal"}})
        self.assertEqual(left["rows"][0]["amount"], "1.2")
        self.assertEqual(left["types"], {"amount": "decimal"})
        self.assertTrue(module.compare(left, right, {"mode": "key", "keys": ["id"]})["equal"])

    @unittest.skipIf(openpyxl is None, "已有可选依赖 openpyxl 不可用；不安装依赖")
    def test_read_sdk_cross_excel_csv(self):
        workbook = openpyxl.Workbook()
        workbook.active.title = "Data"
        workbook.active.append(["id", "price"])
        workbook.active.append([1, 2.5])
        path = self.root / "right.xlsx"
        workbook.save(path)
        workbook.close()
        left = module._read(str(self.write("left.csv", "id,price\n1,2.5000\n")), {}, {"types": {"id": "integer", "price": "decimal"}})
        right = module._read(str(path), {"sheet": "Data"}, {"types": {"id": "integer", "price": "decimal"}})
        self.assertTrue(module.compare(left, right, {})["equal"])
        self.assertEqual(right["locations"][0]["sheet"], "Data")

    def test_read_passes_sdk_options_without_default_override(self):
        options = {"format": "json"}
        raw = dataset([{"id": 1}])
        with patch.object(module, "read_dataset", return_value=raw) as reader:
            normalized = module._read("fake.json", options, {})
        reader.assert_called_once_with("fake.json", options)
        self.assertEqual(options, {"format": "json"})
        self.assertEqual(normalized["rows"], raw["rows"])

    def test_empty_datasets_and_all_ignored_columns(self):
        self.assertTrue(module.compare(dataset([]), dataset([]), {})["equal"])
        self.assertTrue(self.compare([{"v": 1}], [{"v": 2}], ignore_columns=["v"])["equal"])
        self.assertFalse(self.compare([{"v": 1}], [{"v": 2}, {"v": 3}], mode="multiset", ignore_columns=["v"])["equal"])

    def test_incomplete_sdk_result_is_rejected(self):
        for fake in ({**dataset([]), "complete": False}, {**dataset([{"id": 1}]), "locations": []}):
            with patch.object(module, "read_dataset", return_value=fake), self.assertRaises(PluginError):
                module._read("fake.json", {}, {})


class RuntimeHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="testbox-compare-host-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Hermetic root: copy this plugin plus the official preview plugin and
        # Python package. Never copy other concurrently-developed P1 plugins.
        for name in ("data-compare", "data-preview"):
            shutil.copytree(ROOT / "plugins" / name, self.root / "plugins" / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "testbox", self.root / "testbox", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_bundled_plugins"))
        self.runtime = Runtime(self.root)
        self.addCleanup(self.runtime.close)

    def write(self, name, content):
        path = self.root / name
        if isinstance(content, (list, dict)):
            content = json.dumps(content, ensure_ascii=False, allow_nan=False)
        path.write_text(content, encoding="utf-8", newline="")
        return path

    def run_compare(self, left, right, **params):
        return self.runtime.run("data.compare", {"left": str(left), "right": str(right), **params})

    def report(self, task_id, result):
        self.assertEqual(result.status, "success", result.to_dict())
        self.assertEqual(result.files, [f"{task_id}.json", f"{task_id}.csv", f"{task_id}.md"])
        return json.loads((self.root / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"),
                          parse_constant=lambda value: self.fail(f"输出存在非有限数值: {value}"))

    def test_manifest_schema_contract_and_preview_metadata(self):
        self.assertIn("data.compare", self.runtime.manager.available)
        self.assertEqual(self.runtime.list_unavailable_plugins(), [])
        schema = self.runtime.get_command_schema("data.compare")
        for name in ("left", "right"):
            self.assertIn(name, schema["required"])
            self.assertEqual(schema["properties"][name]["format"], "file-path")
        for name in ("left_options", "right_options", "left_normalize", "right_normalize"):
            self.assertTrue(schema["properties"][name]["additionalProperties"])
            self.assertEqual(schema["properties"][name]["default"], {})
        self.assertEqual(schema["x-preview"], {"command": "data.preview", "sources": [
            {"input": "left", "options": "left_options", "normalize": "left_normalize", "label": "左侧"},
            {"input": "right", "options": "right_options", "normalize": "right_normalize", "label": "右侧"}]})
        manifest = (PLUGIN / "manifest.yaml").read_text(encoding="utf-8")
        self.assertIn('core_compatibility: ">=1.0.16,<2.0"', manifest)
        self.assertIn("network: false", manifest)

    def test_cross_csv_json_full_runtime_trace_and_original_unchanged(self):
        left = self.write("左.csv", "ID,amount,label\n001,1.20, Test \n002,2.0,b\n")
        right = self.write("右.json", [{"id": "002", "amount": 2, "label": "b"}, {"id": "001", "amount": 1.2, "label": "test"}])
        originals = (left.read_bytes(), right.read_bytes())
        task, result = self.run_compare(left, right, mode="key", keys=["id"],
                                       left_normalize={"column_mapping": {"ID": "id"}, "trim": True, "casefold": True, "types": {"amount": "decimal"}},
                                       right_normalize={"types": {"amount": "decimal"}})
        report = self.report(task, result)
        self.assertTrue(report["equal"])
        self.assertEqual(report["sources"]["left"]["types"], {"amount": "decimal"})
        self.assertEqual((left.read_bytes(), right.read_bytes()), originals)
        self.assertEqual(self.runtime.get_task(task)["status"], "SUCCEEDED")
        self.assertEqual(self.runtime.get_task_result(task)["status"], "success")
        self.assertTrue((self.root / "workspace" / task / "report.md").exists())
        self.assertNotIn("differences", result.data)

    def test_difference_is_success_and_locations_from_staged_sources(self):
        left = self.write("left.csv", "id,v\n1,A\n2,B\n")
        right = self.write("right.json", [{"id": "1", "v": "changed"}, {"id": "3", "v": "C"}])
        task, result = self.run_compare(left, right, mode="key", keys=["id"])
        report = self.report(task, result)
        self.assertFalse(result.data["equal"])
        self.assertEqual(self.runtime.get_task(task)["status"], "SUCCEEDED")
        self.assertEqual((report["changed_fields"], report["missing_rows"], report["added_rows"]), (1, 1, 1))
        detail = report["differences"][0]
        self.assertEqual(detail["left_location"]["row"], 2)
        self.assertEqual(detail["right_location"]["row"], 1)
        self.assertTrue(Path(detail["left_location"]["source"]).is_file())

    def test_max_differences_full_count_and_report_warning(self):
        left = self.write("left.json", [{"v": i} for i in range(20)])
        right = self.write("right.json", [{"v": -1} for _ in range(20)])
        task, result = self.run_compare(left, right, max_differences=1)
        report = self.report(task, result)
        self.assertEqual(report["difference_count"], 20)
        self.assertEqual(len(report["differences"]), 1)
        self.assertTrue(report["truncated"])
        self.assertTrue(result.warnings)
        text = (self.root / "workspace" / task / "output" / f"{task}.md").read_text(encoding="utf-8")
        self.assertIn("相等判断仍基于全量", text)

    def test_primary_key_duplicate_missing_empty_and_normalization_conflicts(self):
        right = self.write("right.json", [{"id": "a"}])
        cases = [([{"id": "a"}, {"id": "a"}], {}, "KEY_DUPLICATE"),
                 ([{"id": "a"}, {}], {}, "KEY_INVALID"), ([{"id": None}], {}, "KEY_INVALID"),
                 ([{"id": ""}], {}, "KEY_INVALID"),
                 ([{"id": " A "}, {"id": "a"}], {"trim": True, "casefold": True}, "KEY_DUPLICATE"),
                 ([{"id": "01"}, {"id": "1"}], {"types": {"id": "integer"}}, "KEY_DUPLICATE")]
        for index, (rows, rules, expected) in enumerate(cases):
            with self.subTest(rows=rows):
                task, result = self.run_compare(self.write(f"left{index}.json", rows), right, mode="key", keys=["id"], left_normalize=rules)
                self.assertEqual(result.status, "failed", result.to_dict())
                self.assertEqual(result.data["error_code"], expected)
                self.assertEqual(self.runtime.get_task(task)["status"], "FAILED")
                self.assertEqual(result.files, [])

    def test_column_mapping_collision_fails_in_sdk(self):
        left = self.write("left.json", [{"ID": 1, "id": 2}])
        right = self.write("right.json", [{"id": 1}])
        _, result = self.run_compare(left, right, left_normalize={"column_mapping": {"ID": "id"}})
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["error_code"], "INVALID_PARAMS")

    def test_multiset_count_and_tolerance_real_host(self):
        left = self.write("left.json", [{"v": 1}, {"v": 0}])
        right = self.write("right.json", [{"v": 2}, {"v": 1}])
        task, result = self.run_compare(left, right, mode="multiset", absolute_tolerance=1)
        self.assertTrue(self.report(task, result)["equal"])
        task, result = self.run_compare(left, self.write("duplicates.json", [{"v": 1}, {"v": 1}]), mode="multiset")
        report = self.report(task, result)
        self.assertFalse(report["equal"])
        self.assertEqual((report["missing_rows"], report["added_rows"]), (1, 1))

    def test_preview_and_compare_use_same_options_and_normalization(self):
        source = self.write("source.txt", 'ID||name<END>01||" A "<END>')
        options = {"format": "txt", "delimiter": "||", "record_separator": "<END>", "skip_empty_rows": False}
        rules = {"column_mapping": {"ID": "id"}, "trim": True, "casefold": True}
        task, preview = self.runtime.run("data.preview", {"input": str(source), "options": options, "normalize": rules})
        self.assertEqual(preview.status, "success", preview.to_dict())
        self.assertEqual(preview.data["rows"], [{"id": "01", "name": "a"}])
        target = self.write("target.json", preview.data["rows"])
        task, result = self.run_compare(source, target, left_options=options, left_normalize=rules)
        self.assertTrue(self.report(task, result)["equal"])

    def test_txt_custom_separators_escaped_newlines_and_json_path(self):
        source = self.write("source.txt", 'id||name\r\n1||"line\r\nbreak"\r\n')
        target = self.write("target.json", {"data": {"rows": [{"id": "1", "name": "line\r\nbreak"}]}})
        task, result = self.run_compare(source, target, left_options={"delimiter": "||", "record_separator": "\\r\\n"}, right_options={"json_path": "data.rows"})
        self.assertTrue(self.report(task, result)["equal"])

    def test_jsonl_and_tsv_cross_format(self):
        left = self.write("left.tsv", "id\tv\n1\tA\n2\tB\n")
        right = self.write("right.jsonl", '{"id":"1","v":"A"}\n{"id":"2","v":"B"}\n')
        task, result = self.run_compare(left, right)
        self.assertTrue(self.report(task, result)["equal"])

    def test_strict_json_rejects_duplicate_nan_infinity_and_depth(self):
        right = self.write("valid.json", [{"v": 1}])
        for index, text in enumerate(('[{"v":1,"v":2}]', '[{"v":NaN}]', '[{"v":Infinity}]', '[{"v":' + '[' * 65 + '0' + ']' * 65 + '}]')):
            with self.subTest(text=text):
                _, result = self.run_compare(self.write(f"bad{index}.json", text), right)
                self.assertEqual(result.status, "failed", result.to_dict())
                self.assertNotEqual(result.data["error_code"], "EXECUTION_FAILED")
                self.assertEqual(result.files, [])

    def test_no_silent_row_drop_or_input_limit_truncation(self):
        right = self.write("valid.json", [{"v": "1"}])
        for index, (text, options) in enumerate((("v\n1\n2\n", {"max_rows": 1}),
                                               ("v\n1\n", {"max_bytes": 2}),
                                               ("v,x\n1,2\n", {"max_columns": 1}),
                                               ("v,x\n1\n", {}), ("v,x\n1,2,3\n", {}),
                                               ("v,x\n1,2\n\n", {"skip_empty_rows": False}))):
            with self.subTest(text=text, options=options):
                _, result = self.run_compare(self.write(f"left{index}.csv", text), right, left_options=options)
                self.assertEqual(result.status, "failed", result.to_dict())
                self.assertEqual(result.files, [])

    def test_jsonl_blank_record_default_skip_and_explicit_false(self):
        left = self.write("left.jsonl", '{"id":1}\n\n{"id":2}\n')
        right = self.write("right.json", [{"id": 1}, {"id": 2}])
        _, result = self.run_compare(left, right, left_options={"skip_empty_rows": False})
        self.assertEqual(result.status, "failed")
        task, result = self.run_compare(left, right)
        self.assertTrue(self.report(task, result)["equal"])

    def test_blank_row_defaults_match_preview_and_compare(self):
        # A blank single-column CSV record is preserved as empty string when
        # requested; multi-column CSV blanks fail width validation. JSONL blank
        # records cannot be represented as object rows, so false rejects them.
        cases = [
            ("single.csv", "v\nA\n\nB\n", [{"v": "A"}, {"v": "B"}], [{"v": "A"}, {"v": ""}, {"v": "B"}]),
            ("multi.csv", "id,v\n1,A\n\n2,B\n", [{"id": "1", "v": "A"}, {"id": "2", "v": "B"}], None),
            ("records.jsonl", '{"v":"A"}\n\n{"v":"B"}\n', [{"v": "A"}, {"v": "B"}], None),
        ]
        for name, text, default_rows, preserved_rows in cases:
            source = self.write(name, text)
            for options in ({}, {"skip_empty_rows": True}, {"skip_empty_rows": False}):
                with self.subTest(name=name, options=options):
                    _, preview = self.runtime.run("data.preview", {"input": str(source), "options": options})
                    expected = preserved_rows if options.get("skip_empty_rows") is False else default_rows
                    target = self.write("blank-target.json", expected or default_rows)
                    task, compared = self.run_compare(source, target, left_options=options)
                    self.assertEqual(compared.status, preview.status)
                    if expected is None:
                        self.assertEqual(preview.status, "failed", preview.to_dict())
                        self.assertEqual(preview.data["error_code"], compared.data["error_code"])
                    else:
                        self.assertEqual(preview.status, "success", preview.to_dict())
                        self.assertEqual(preview.data["rows"], expected)
                        self.assertEqual(preview.data["row_count"], len(expected))
                        report = self.report(task, compared)
                        self.assertTrue(report["equal"])
                        self.assertEqual(report["left_rows"], preview.data["row_count"])

    def test_csv_formula_safety_json_preserves_values_and_no_nan(self):
        value = "  =SUM(1,1)"
        left = self.write("left.json", [{"v": value}])
        right = self.write("right.json", [{"v": "different"}])
        original = left.read_bytes()
        task, result = self.run_compare(left, right)
        report = self.report(task, result)
        self.assertEqual(report["differences"][0]["left"], value)
        csv_path = self.root / "workspace" / task / "output" / f"{task}.csv"
        with csv_path.open(encoding="utf-8", newline="") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["left"], "'" + value)
        self.assertEqual(left.read_bytes(), original)
        for file in (f"{task}.json", "../result.json"):
            path = (self.root / "workspace" / task / "output" / file).resolve()
            json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda v: self.fail(v))

    def test_invalid_options_and_schema_limits(self):
        left = self.write("left.csv", "v\n1\n")
        right = self.write("right.json", [{"v": 1}])
        for options in ({"normalized_decimals": True}, {"delimiter": "||"}):
            _, result = self.run_compare(left, right, left_options=options)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.data["error_code"], "INVALID_PARAMS")
        for params in ({"max_differences": 100001}, {"absolute_tolerance": -1}, {"relative_tolerance": float("nan")}, {"left_options": {"extra": float("inf")}}):
            with self.subTest(params=params), self.assertRaises(SchemaValidationError):
                self.runtime.validate_params("data.compare", {"left": str(left), "right": str(right), **params})

    @unittest.skipIf(openpyxl is None, "已有可选依赖 openpyxl 不可用；不安装依赖")
    def test_cross_excel_csv_and_json_with_sheet_and_header_options(self):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "Selected"
        sheet.append(["metadata", "metadata"])
        sheet.append(["ID", "amount"])
        sheet.append([1, 2.5])
        sheet.append([2, 3.25])
        path = self.root / "sheet.xlsx"
        book.save(path)
        book.close()
        originals = path.read_bytes()
        csv_path = self.write("left.csv", "id,amount\n1,2.5000\n2,3.250\n")
        rules = {"types": {"id": "integer", "amount": "decimal"}}
        excel_rules = {**rules, "column_mapping": {"ID": "id"}}
        for other in (csv_path, self.write("left.json", [{"id": 1, "amount": 2.5}, {"id": 2, "amount": 3.25}])):
            with self.subTest(other=other):
                task, result = self.run_compare(other, path, left_normalize=rules, right_normalize=excel_rules,
                                               right_options={"sheet": "Selected", "header_row": 2})
                self.assertTrue(self.report(task, result)["equal"])
        self.assertEqual(path.read_bytes(), originals)

    @unittest.skipIf(openpyxl is None, "已有可选依赖 openpyxl 不可用；不安装依赖")
    def test_excel_formulas_not_evaluated_and_cached_mode_warns(self):
        book = openpyxl.Workbook()
        book.active.append(["id", "v"])
        book.active.append([1, "=1+1"])
        path = self.root / "formula.xlsx"
        book.save(path)
        book.close()
        formula = self.write("formula.json", [{"id": 1, "v": "=1+1"}])
        task, result = self.run_compare(path, formula)
        self.assertTrue(self.report(task, result)["equal"])
        task, result = self.run_compare(path, self.write("cached.json", [{"id": 1, "v": None}]), left_options={"formula_mode": "cached"})
        self.assertTrue(self.report(task, result)["equal"])
        self.assertTrue(result.warnings)

    @unittest.skipIf(openpyxl is None, "已有可选依赖 openpyxl 不可用；不安装依赖")
    def test_excel_blank_rows_preview_and_compare_same_options(self):
        book = openpyxl.Workbook()
        book.active.append(["id", "v"])
        book.active.append([1, "A"])
        book.active.append([None, None])
        book.active.append([2, "B"])
        source = self.root / "blank.xlsx"
        book.save(source)
        book.close()
        original = source.read_bytes()
        for options in ({}, {"skip_empty_rows": True}, {"skip_empty_rows": False}):
            with self.subTest(options=options):
                _, preview = self.runtime.run("data.preview", {"input": str(source), "options": options})
                self.assertEqual(preview.status, "success", preview.to_dict())
                task, compared = self.run_compare(source, self.write("blank-excel.json", preview.data["rows"]), left_options=options)
                report = self.report(task, compared)
                self.assertTrue(report["equal"])
                self.assertEqual(report["left_rows"], 3 if options.get("skip_empty_rows") is False else 2)
                self.assertEqual(report["left_rows"], preview.data["row_count"])
        self.assertEqual(source.read_bytes(), original)

    def test_raw_host_plugin_error_protocol_and_unknown_command(self):
        workspace = self.root / "direct-host"
        for name in ("input", "output", "logs"):
            (workspace / name).mkdir(parents=True)
        request = {"protocol_version": 1, "task_id": "direct-test", "plugin_path": str(self.root / "plugins" / "data-compare"),
                   "entry": "src.main:Plugin", "command": "unknown.command", "params": {}, "config": {}, "workspace": str(workspace)}
        process = subprocess.run([sys.executable, "-m", "testbox.core.host"], input=json.dumps(request), text=True,
                                 cwd=self.root, capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        event = json.loads(process.stdout)
        self.assertEqual(event["event"], "result")
        self.assertEqual(event["result"]["status"], "failed")
        self.assertEqual(event["result"]["data"]["error_code"], "INVALID_PARAMS")


if __name__ == "__main__":
    unittest.main()
