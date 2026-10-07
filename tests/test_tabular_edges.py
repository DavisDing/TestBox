"""High-risk parser/normalization boundaries exercised only through the public SDK.

Fixtures are real temporary files, including openpyxl workbooks when the existing
optional dependency is available.  No parser internals or plugin mocks are used.
"""
from __future__ import annotations

import copy
import csv
from decimal import Inexact, Rounded, localcontext
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from testbox.sdk import PluginError, normalize_dataset, read_dataset


HAS_EXCEL = importlib.util.find_spec("openpyxl") is not None


class DatasetFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def source(self, text, suffix=".json"):
        path = self.root / ("source" + suffix)
        path.write_bytes(text.encode("utf-8"))
        return path

    def read(self, path, options=None):
        """Check read-only behavior on success AND on all exception paths."""
        before = path.read_bytes()
        modified = path.stat().st_mtime_ns
        siblings = set(path.parent.iterdir())
        original_options = copy.deepcopy(options)
        try:
            return read_dataset(path, options)
        finally:
            self.assertEqual(path.read_bytes(), before, "SDK must not rewrite its source")
            self.assertEqual(path.stat().st_mtime_ns, modified, "SDK must not touch its source")
            self.assertEqual(set(path.parent.iterdir()), siblings, "SDK must not create sidecars")
            self.assertEqual(options, original_options, "SDK must not mutate parser options")

    def normalize(self, dataset, rules=None):
        original = copy.deepcopy(dataset)
        original_rules = copy.deepcopy(rules)
        try:
            return normalize_dataset(dataset, rules)
        finally:
            self.assertEqual(dataset, original, "Normalization must not mutate the dataset")
            self.assertEqual(rules, original_rules, "Normalization must not mutate rules")

    def assert_complete(self, dataset, rows):
        self.assertIs(dataset["complete"], True)
        self.assertEqual(dataset["rows"], rows)
        self.assertEqual(len(dataset["locations"]), len(rows))


class TabularEdgeTests(DatasetFixtures):
    def test_sparse_json_distinguishes_missing_null_and_empty_after_mapping(self):
        path = self.source('[{}, {"v":null}, {"v":""}, {"v":" NULL "}, {"later":"001"}]')
        before = path.read_bytes()
        data = self.read(path)
        self.assertEqual(data["columns"], ["v", "later"])
        result = self.normalize(data, {"column_mapping": {"v": "value"}})
        self.assert_complete(result, [{}, {"value": None}, {"value": ""},
                                      {"value": " NULL "}, {"later": "001"}])
        # Empty strings are not implicitly null during numeric conversion.
        with self.assertRaises(PluginError):
            self.normalize(data, {
                "column_mapping": {"v": "value"}, "trim": True,
                "null_values": ["NULL"], "types": {"value": "decimal"},
            })
        self.assertEqual(path.read_bytes(), before)

    def test_sparse_json_explicit_null_conversion_does_not_fill_missing_fields(self):
        data = self.read(self.source('[{}, {"v":null}, {"v":""}, {"v":" NULL "}, {"later":"001"}]'))
        result = self.normalize(data, {
            "column_mapping": {"v": "value"}, "trim": True,
            "null_values": ["", "NULL"], "types": {"value": "decimal"},
        })
        self.assertEqual(result["columns"], ["value", "later"])
        self.assert_complete(result, [{}, {"value": None}, {"value": None},
                                      {"value": None}, {"later": "001"}])
        self.assertEqual(result["locations"], data["locations"])
        self.assertEqual(result["types"], {"value": "decimal"})

    def test_empty_json_can_have_explicit_columns_without_fabricated_rows(self):
        result = self.read(self.source("[]"), {"columns": ["id", "value"]})
        self.assertEqual(result["columns"], ["id", "value"])
        self.assert_complete(result, [])
        normalized = self.normalize(result, {"column_mapping": {"id": "key"}})
        self.assertEqual(normalized["columns"], ["key", "value"])
        self.assert_complete(normalized, [])

    def test_json_empty_field_name_and_truncated_documents_are_rejected(self):
        for text in ('[{"":1}]', '[{"id":1}', '{"id":', '[{"id":1},]',
                     '[{"id":1}] trailing', "", " \n\t"):
            with self.subTest(text=text), self.assertRaises(PluginError):
                self.read(self.source(text))

    def test_empty_json_explicit_invalid_columns_are_rejected(self):
        for columns in ("id", [""], ["id", "id"], ["id", None], [7]):
            with self.subTest(columns=columns), self.assertRaises(PluginError):
                self.read(self.source("[]"), {"columns": columns})

    def test_json_duplicate_escaped_and_nested_keys_are_not_silently_overwritten(self):
        cases = (
            '[{"x":1,"\\u0078":2}]',
            '[{"id":1,"payload":{"x":1,"x":2}}]',
            '{"data":[{"id":1}],"discarded":{"x":1,"x":2}}',
        )
        for text in cases:
            for suffix in (".json", ".jsonl"):
                with self.subTest(text=text, suffix=suffix), self.assertRaises(PluginError):
                    # JSONL must contain an object, not an array whose invalid
                    # shape would conceal a failure to detect duplicate keys.
                    record = text[1:-1] if suffix == ".jsonl" and text.startswith("[") else text
                    self.read(self.source(record, suffix),
                              {"json_path": "data"} if "discarded" in text else {})

    def test_jsonl_valid_prefix_does_not_hide_invalid_final_record(self):
        for last in ('{"v":', '{"v":{"x":1,"x":2}}', '{"v":Infinity}'):
            with self.subTest(last=last), self.assertRaises(PluginError):
                self.read(self.source('{"v":1}\n\n' + last, ".jsonl"))

    def test_json_nonfinite_values_in_nested_or_unselected_regions_fail(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
            cases = (
                (".json", '[{"v":{"nested":[' + value + ']}}]', {}),
                (".jsonl", '{"v":{"nested":[' + value + ']}}', {}),
                (".json", '{"data":[{"v":1}],"other":' + value + '}',
                 {"json_path": "data"}),
            )
            for suffix, text, options in cases:
                with self.subTest(value=value, suffix=suffix), self.assertRaises(PluginError):
                    self.read(self.source(text, suffix), options)

    def test_json_deep_nesting_fails_as_plugin_error_not_recursion_error(self):
        for depth in (80, 1500):
            nested = "[" * depth + "0" + "]" * depth
            for suffix, text in ((".json", '[{"v":' + nested + '}]'),
                                 (".jsonl", '{"v":' + nested + '}')):
                with self.subTest(depth=depth, suffix=suffix), self.assertRaises(PluginError):
                    self.read(self.source(text, suffix))

    def test_json_supported_nested_cells_remain_independent_after_normalization(self):
        data = self.read(self.source('[{"v":{"nested":[1,null,{"text":" Keep "}]}}]'))
        normalized = self.normalize(data, {"trim": True})
        self.assertEqual(normalized["rows"], data["rows"])
        normalized["rows"][0]["v"]["nested"][2]["text"] = "changed"
        self.assertEqual(data["rows"][0]["v"]["nested"][2]["text"], " Keep ")

    def test_json_path_invalid_types_missing_regions_and_huge_indices_fail(self):
        path = self.source('{"data":[{"v":1}]}')
        for json_path in (None, 1, [], "data.9", "data.missing", "missing", "data." + "9" * 5000):
            with self.subTest(path=str(json_path)[:60]), self.assertRaises(PluginError):
                self.read(path, {"json_path": json_path})

    def test_csv_preserves_quoted_cr_crlf_and_commas(self):
        for newline in ("\r", "\r\n"):
            value = "a" + newline + 'b,c"d'
            stream = io.StringIO(newline="")
            writer = csv.writer(stream, lineterminator=newline)
            writer.writerows([["id", "v"], ["001", value], ["002", "tail"]])
            with self.subTest(newline=repr(newline)):
                data = self.read(self.source(stream.getvalue(), ".csv"))
                self.assert_complete(data, [{"id": "001", "v": value},
                                           {"id": "002", "v": "tail"}])

    def test_csv_locations_count_bare_cr_inside_quoted_field(self):
        data = self.read(self.source('id,v\r001,"a\rb,c"\r002,tail\r', ".csv"))
        self.assertEqual([location["row"] for location in data["locations"]], [2, 4])

    def test_csv_locations_count_crlf_inside_quoted_field_once(self):
        data = self.read(self.source('id,v\r\n001,"a\r\nb,c"\r\n002,tail\r\n', ".csv"))
        self.assertEqual([location["row"] for location in data["locations"]], [2, 4])

    def test_csv_empty_trailing_cells_are_not_missing_or_skipped(self):
        data = self.read(self.source("id,v,last\n001,,\n,,\n", ".csv"))
        self.assert_complete(data, [{"id": "001", "v": "", "last": ""},
                                   {"id": "", "v": "", "last": ""}])

    def test_csv_empty_column_names_are_rejected_even_with_no_data(self):
        for header in ("id,\n", ",id\n", "id,,v\n"):
            with self.subTest(header=header), self.assertRaises(PluginError):
                self.read(self.source(header, ".csv"))

    def test_txt_escaped_multichar_delimiter_separator_and_quotes_are_literal(self):
        text = 'id||v@@1||a\\||b@@2||c\\@@d@@3||"x\\"y\\\\z"@@4||""@@'
        data = self.read(self.source(text, ".txt"), {
            "delimiter": "||", "record_separator": "@@", "escapechar": "\\",
        })
        self.assert_complete(data, [{"id": "1", "v": "a||b"},
                                   {"id": "2", "v": "c@@d"},
                                   {"id": "3", "v": 'x"y\\z'},
                                   {"id": "4", "v": ""}])

    def test_txt_multichar_separator_disabled_quotes_and_doubled_quotes(self):
        cases = (
            ('id||v@@1||a"b@@', {"quotechar": ""}, 'a"b'),
            ('id||v@@1||"a""b||c"@@', {}, 'a"b||c'),
        )
        for text, extra, value in cases:
            with self.subTest(extra=extra):
                data = self.read(self.source(text, ".txt"), {
                    "delimiter": "||", "record_separator": "@@", **extra,
                })
                self.assert_complete(data, [{"id": "1", "v": value}])

    def test_txt_dangling_escape_and_incomplete_quotes_fail_after_valid_prefix(self):
        for tail in ('2||unfinished\\', '2||"unfinished\\', '2||"x"junk'):
            with self.subTest(tail=tail), self.assertRaises(PluginError):
                self.read(self.source("id||v@@1||ok@@" + tail, ".txt"), {
                    "delimiter": "||", "record_separator": "@@", "escapechar": "\\",
                })

    def test_txt_escaped_physical_newline_preserves_next_row_location(self):
        data = self.read(self.source("id||v\n1||a\\\nb\n2||tail\n", ".txt"), {
            "delimiter": "||", "escapechar": "\\",
        })
        self.assert_complete(data, [{"id": "1", "v": "a\nb"},
                                   {"id": "2", "v": "tail"}])
        self.assertEqual([location["row"] for location in data["locations"]], [2, 4])

    def test_parser_invalid_configuration_and_unknown_options_are_plugin_errors(self):
        path = self.source("id,v\n1,ok\n", ".csv")
        cases = (
            [], "options", {"unknown": True}, {"format": []}, {"format": "xml"},
            {"encoding": 1}, {"has_header": 1}, {"skip_empty_rows": "false"},
            {"delimiter": None}, {"delimiter": ""}, {"delimiter": "||"},
            {"quotechar": []}, {"quotechar": "ab"}, {"escapechar": {}},
            {"escapechar": "ab"}, {"record_separator": []},
            {"record_separator": ""}, {"record_separator": ","},
            {"record_separator": ",,"}, {"delimiter": '"'},
            {"start_row": 1, "header_row": 1},
        )
        for options in cases:
            with self.subTest(options=options):
                with self.assertRaises(PluginError) as caught:
                    self.read(path, options)
                self.assertEqual(caught.exception.code, "INVALID_PARAMS")

    def test_parser_integer_limits_reject_bool_fraction_negative_and_string(self):
        path = self.source("id\n1\n", ".csv")
        for key in ("max_rows", "max_columns", "max_bytes", "max_cells",
                    "max_cell_length", "header_row", "start_row"):
            for value in (True, -1, 1.5, "2", None):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(PluginError) as caught:
                        self.read(path, {key: value})
                    self.assertEqual(caught.exception.code, "INVALID_PARAMS")

    def test_parser_numeric_limits_reject_zero_and_excessive_configuration(self):
        path = self.source("id\n1\n", ".csv")
        upper_bounds = {"max_rows": 1_000_000, "max_columns": 4096,
                        "max_bytes": 100 * 1024 * 1024, "max_cells": 10_000_000,
                        "max_cell_length": 1_000_000, "header_row": 1_000_000,
                        "start_row": 1_000_000}
        for key, upper in upper_bounds.items():
            invalid = (upper + 1,) if key == "start_row" else (0, upper + 1)
            for value in invalid:
                with self.subTest(key=key, value=value):
                    with self.assertRaises(PluginError) as caught:
                        self.read(path, {key: value})
                    self.assertEqual(caught.exception.code, "INVALID_PARAMS")

    def test_no_header_invalid_columns_are_not_inferred_silently(self):
        path = self.source("1,2\n", ".csv")
        for columns in ("id", ["id", ""], ["id", "id"], ["id", 2]):
            with self.subTest(columns=columns), self.assertRaises(PluginError):
                self.read(path, {"has_header": False, "columns": columns})

    def test_normalization_invalid_configuration_is_not_a_raw_type_error(self):
        data = self.read(self.source('[{"v":"1"}]'))
        cases = (
            [], "rules", {"unknown": 1}, {"trim": 1}, {"casefold": "true"},
            {"column_mapping": []}, {"column_mapping": {"missing": "v"}},
            {"column_mapping": {"v": ""}}, {"column_mapping": {"v": None}},
            {"types": []}, {"types": {"v": "float"}}, {"types": {"v": []}},
            {"types": {"v": {}}}, {"null_values": "NULL"},
        )
        for rules in cases:
            with self.subTest(rules=rules):
                with self.assertRaises(PluginError) as caught:
                    self.normalize(data, rules)
                self.assertEqual(caught.exception.code, "INVALID_PARAMS")

    def test_late_conversion_failure_does_not_mutate_source_or_prior_rows(self):
        path = self.source('[{"v":" 001 "},{"v":"not-a-number"}]')
        before = path.read_bytes()
        data = self.read(path)
        with self.assertRaises(PluginError):
            self.normalize(data, {"trim": True, "types": {"v": "integer"}})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(data["rows"], [{"v": " 001 "}, {"v": "not-a-number"}])

    def test_successful_normalization_does_not_rewrite_source(self):
        path = self.source('[{"v":" 001.200 "}]')
        before = path.read_bytes()
        modified = path.stat().st_mtime_ns
        data = self.read(path)
        result = self.normalize(data, {"trim": True, "types": {"v": "decimal"}})
        self.assert_complete(result, [{"v": "1.2"}])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, modified)

    def test_exact_row_byte_column_and_cell_limits_preserve_all_formats(self):
        cases = (
            (".csv", "a,b\n1,2\n3,4\n", {}),
            (".txt", "a||b@@1||2@@3||4@@", {"delimiter": "||", "record_separator": "@@"}),
            (".json", '[{"a":"1","b":"2"},{"a":"3","b":"4"}]', {}),
            (".jsonl", '{"a":"1","b":"2"}\n{"a":"3","b":"4"}', {}),
        )
        for suffix, text, options in cases:
            with self.subTest(suffix=suffix):
                path = self.source(text, suffix)
                limits = {"max_rows": 2, "max_columns": 2, "max_cells": 4,
                          "max_bytes": path.stat().st_size}
                data = self.read(path, {**options, **limits})
                self.assert_complete(data, [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}])
                for key, limit in limits.items():
                    with self.subTest(limit=key), self.assertRaises(PluginError):
                        self.read(path, {**options, **limits, key: limit - 1})

    def test_json_limit_counts_present_cells_without_inventing_missing_values(self):
        path = self.source('[{"a":1},{"b":2}]')
        data = self.read(path, {"max_rows": 2, "max_columns": 2, "max_cells": 2})
        self.assert_complete(data, [{"a": 1}, {"b": 2}])
        for options in ({"max_cells": 1}, {"max_columns": 1}, {"max_rows": 1}):
            with self.subTest(options=options), self.assertRaises(PluginError):
                self.read(path, options)

    def test_max_cell_length_uses_decoded_content_not_json_quotes_or_escapes(self):
        for value in ("abc", "汉字测", "a\nb"):
            for suffix in (".json", ".csv"):
                if suffix == ".json":
                    text = json.dumps([{"v": value}], ensure_ascii=False)
                else:
                    stream = io.StringIO(newline="")
                    csv.writer(stream).writerows([["v"], [value]])
                    text = stream.getvalue()
                with self.subTest(value=value, suffix=suffix):
                    path = self.source(text, suffix)
                    data = self.read(path, {"max_cell_length": 3})
                    self.assert_complete(data, [{"v": value}])
                    with self.assertRaises(PluginError):
                        self.read(path, {"max_cell_length": 2})

    def test_header_only_csv_cannot_bypass_field_name_length_limit(self):
        path = self.source("oversized\n", ".csv")
        with self.assertRaises(PluginError):
            self.read(path, {"max_cell_length": 3})

    def test_decimal_is_exact_under_small_ambient_precision_and_rounding_traps(self):
        cases = {
            "987654321098765432109876543210.1234567890123456789000":
                "987654321098765432109876543210.1234567890123456789",
            "1.234567890123456789e-25": "0.0000000000000000000000001234567890123456789",
            "-0.0000": "0",
            "1000000000000000000000000000000.000": "1000000000000000000000000000000",
        }
        for value, expected in cases.items():
            with self.subTest(value=value), localcontext() as context:
                context.prec = 4
                context.traps[Inexact] = True
                context.traps[Rounded] = True
                data = self.read(self.source(json.dumps([{"v": value}])))
                result = self.normalize(data, {"types": {"v": "decimal"}})
                self.assertEqual(result["rows"][0]["v"], expected)
                self.assertIsInstance(result["rows"][0]["v"], str)
                self.assertEqual(result["types"], {"v": "decimal"})

    def test_integer_conversion_is_exact_under_small_decimal_context(self):
        with localcontext() as context:
            context.prec = 3
            data = self.read(self.source('[{"v":"123456789012345678901234567890.000"}]'))
            result = self.normalize(data, {"types": {"v": "integer"}})
            self.assertEqual(result["rows"][0]["v"], 123456789012345678901234567890)
            self.assertIs(type(result["rows"][0]["v"]), int)

    def test_numeric_conversion_rejects_nonfinite_boolean_and_container_values(self):
        for value in ("NaN", "sNaN", "Infinity", "-Infinity", True, False, {}, []):
            data = self.read(self.source(json.dumps([{"v": value}])))
            for kind in ("decimal", "integer"):
                with self.subTest(value=value, kind=kind), self.assertRaises(PluginError):
                    self.normalize(data, {"types": {"v": kind}})

    def test_huge_exponents_are_rejected_in_bounded_sdk_subprocess(self):
        # A regression must not stall the test runner.  A million-digit expansion
        # is already unsafe for a scalar; avoid billion-digit allocations even
        # when testing an implementation that accidentally removes its guard.
        script = """
import json
import sys
from testbox.sdk import PluginError, read_dataset, normalize_dataset
source = sys.argv[1]
for value in ("1e1000000", "1e-1000000", "0e1000000", "0e-1000000",
              "1e99999999999999999999999999"):
    with open(source, "w", encoding="utf-8") as stream:
        json.dump([{"v": value}], stream)
    with open(source, "rb") as stream:
        before = stream.read()
    dataset = read_dataset(source)
    for kind in ("decimal", "integer"):
        try:
            normalize_dataset(dataset, {"types": {"v": kind}})
        except PluginError:
            pass
        else:
            raise AssertionError((value, kind, "unsafe exponent accepted"))
    with open(source, "rb") as stream:
        assert stream.read() == before, "conversion rewrote its source"
print("ALL_REJECTED")
"""
        project = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.root / "huge.json")],
            cwd=project, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ALL_REJECTED")


@unittest.skipUnless(HAS_EXCEL, "Excel parser requires the existing optional openpyxl dependency")
class ExcelEdgeTests(DatasetFixtures):
    def workbook(self, rows, *, suffix=".xlsx"):
        from openpyxl import Workbook

        book = Workbook()
        self.addCleanup(book.close)
        sheet = book.active
        sheet.title = "Data"
        for row in rows:
            sheet.append(row)
        path = self.root / ("source" + suffix)
        book.save(path)
        return path

    def test_excel_sparse_cells_preserve_nulls_and_physical_source_rows(self):
        path = self.workbook([["id", "v"], ["001"], [None, None], [None, "tail"]])
        data = self.read(path)
        self.assert_complete(data, [{"id": "001", "v": None}, {"id": None, "v": "tail"}])
        self.assertEqual([location["row"] for location in data["locations"]], [2, 4])
        self.assertTrue(all(location["sheet"] == "Data" for location in data["locations"]))
        self.assert_complete(self.read(path, {"skip_empty_rows": False}), [
            {"id": "001", "v": None}, {"id": None, "v": None}, {"id": None, "v": "tail"},
        ])

    def test_excel_data_in_unnamed_extra_column_is_not_silently_dropped(self):
        path = self.workbook([["id", "v"], ["001", "ok", "extra"]])
        with self.assertRaises(PluginError):
            self.read(path)

    def test_truncated_excel_archive_is_rejected_without_rewriting_source(self):
        path = self.workbook([["id", "v"], ["001", "tail"]])
        path.write_bytes(path.read_bytes()[:-32])
        with self.assertRaises(PluginError):
            self.read(path)

    def test_excel_empty_or_duplicate_header_cells_fail(self):
        for header in (["id", None], [None, "id"], ["id", "id"]):
            with self.subTest(header=header):
                path = self.workbook([header, ["001", "tail"]])
                with self.assertRaises(PluginError):
                    self.read(path)

    def test_empty_excel_headerless_explicit_columns_returns_no_phantom_row(self):
        path = self.workbook([])
        with self.assertRaises(PluginError):
            self.read(path)
        data = self.read(path, {"has_header": False, "columns": ["id", "v"]})
        self.assertEqual(data["columns"], ["id", "v"])
        self.assert_complete(data, [])

    def test_excel_blank_header_position_does_not_slide_to_later_row(self):
        path = self.workbook([[None, None], ["id", "v"], ["001", "tail"]])
        with self.assertRaises(PluginError):
            self.read(path, {"header_row": 1})
        data = self.read(path, {"header_row": 2})
        self.assert_complete(data, [{"id": "001", "v": "tail"}])
        self.assertEqual(data["locations"][0]["row"], 3)

    def test_excel_header_only_workbook_is_complete_without_phantom_data(self):
        path = self.workbook([["id", "v"]])
        data = self.read(path)
        self.assertEqual(data["columns"], ["id", "v"])
        self.assert_complete(data, [])

    def test_header_only_excel_cannot_bypass_field_name_length_limit(self):
        path = self.workbook([["oversized"]])
        with self.assertRaises(PluginError):
            self.read(path, {"max_cell_length": 3})

    def test_excel_start_row_uses_physical_positions_across_blank_rows(self):
        path = self.workbook([["id", "v"], ["001", "ignored"], [None, None], ["002", "tail"]])
        data = self.read(path, {"start_row": 3})
        self.assert_complete(data, [{"id": "002", "v": "tail"}])
        self.assertEqual(data["locations"][0]["row"], 4)

    def test_excel_formula_and_cached_mode_never_calculate_or_rewrite(self):
        path = self.workbook([["id", "v"], ["001", "=1+2"]])
        formula = self.read(path, {"formula_mode": "formula"})
        self.assert_complete(formula, [{"id": "001", "v": "=1+2"}])
        cached = self.read(path, {"formula_mode": "cached"})
        self.assert_complete(cached, [{"id": "001", "v": None}])
        self.assertTrue(cached["warnings"], "Missing formula caches require an explicit warning")

    def test_excel_invalid_modes_and_sheet_types_are_plugin_errors(self):
        path = self.workbook([["id"], ["001"]])
        for options in ({"formula_mode": "evaluate"}, {"formula_mode": []},
                        {"formula_mode": {}}, {"sheet": 1}, {"sheet": []}):
            with self.subTest(options=options):
                with self.assertRaises(PluginError) as caught:
                    self.read(path, options)
                self.assertEqual(caught.exception.code, "INVALID_PARAMS")

    def test_excel_exact_limits_complete_or_fail_without_prefix_success(self):
        path = self.workbook([["id", "v"], ["001", "a"], ["002", "b"]])
        limits = {"max_rows": 2, "max_columns": 2, "max_cells": 4,
                  "max_bytes": path.stat().st_size}
        self.assert_complete(self.read(path, limits), [
            {"id": "001", "v": "a"}, {"id": "002", "v": "b"},
        ])
        for key, value in limits.items():
            with self.subTest(key=key), self.assertRaises(PluginError):
                self.read(path, {**limits, key: value - 1})


if __name__ == "__main__":
    unittest.main()
