from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "plugins" / "data-generator" / "src" / "main.py"
SPEC = importlib.util.spec_from_file_location("data_generator_plugin_test", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class FieldListWorkflowTests(unittest.TestCase):
    def test_sparse_excel_coordinates_preserve_shared_strings_and_wide_columns(self):
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sparse.xlsx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{namespace}"><si><t>shared</t></si></sst>')
                archive.writestr("xl/worksheets/sheet1.xml", f'''<worksheet xmlns="{namespace}"><sheetData>
                    <row r="1"><c r="B1" t="s"><v>0</v></c><c r="AA1" t="inlineStr"><is><t>tail</t></is></c></row>
                    <row r="2"><c r="A2" t="b"><v>1</v></c><c r="C2"><v>42</v></c></row>
                </sheetData></worksheet>''')
            rows = MODULE._read_excel_rows(path)
        self.assertEqual(rows[0], ["", "shared"] + [""] * 24 + ["tail"])
        self.assertEqual(rows[1], ["1", "", "42"])

    def test_excel_column_coordinates_are_bounded(self):
        for reference in ("XFE1", "ZZZ1", "AAAA1", "A0", "invalid"):
            with self.subTest(reference=reference), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "invalid.xlsx"
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("xl/worksheets/sheet1.xml", f'''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
                        <row r="1"><c r="{reference}"><v>1</v></c></row>
                    </sheetData></worksheet>''')
                with self.assertRaises(MODULE.PluginError) as error:
                    MODULE._read_excel_rows(path)
                self.assertEqual(error.exception.code, "INPUT_INVALID")

    def test_consumes_sql_parse_json_field_list_without_plugin_import(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "orders.json"
            path.write_text(
                json.dumps([
                    {
                        "table": "orders",
                        "field": "order_id",
                        "type": "BIGINT",
                        "primary_key": True,
                        "auto_increment": True,
                    },
                    {
                        "table": "orders",
                        "field": "amount",
                        "type": "DECIMAL(12,2)",
                        "comment": "订单金额",
                    },
                ], ensure_ascii=False),
                encoding="utf-8",
            )
            tables = MODULE.json_tables(path)

        self.assertEqual([item["table"] for item in tables], ["orders"])
        fields = tables[0]["fields"]
        self.assertEqual([item["name"] for item in fields], ["order_id", "amount"])
        self.assertTrue(fields[0]["primary_key"])
        self.assertEqual(fields[1]["generator"], "decimal_random")
        self.assertEqual(fields[1]["options"]["scale"], 2)

    def test_consumes_sql_parse_csv_field_list_and_groups_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "field-list.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["table", "field", "type", "unique", "options"])
                writer.writerow(["orders", "code", "VARCHAR(8)", "true", json.dumps({"length": 8})])
                writer.writerow(["customers", "email", "VARCHAR(64)", "false", ""])
            tables = MODULE.csv_tables(path)

        self.assertEqual([item["table"] for item in tables], ["orders", "customers"])
        self.assertTrue(tables[0]["fields"][0]["unique"])
        self.assertEqual(tables[0]["fields"][0]["options"]["length"], 8)

    def test_rejects_malformed_field_list_options_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text(json.dumps([{"table": "orders", "field": "code", "options": "not-json"}]), encoding="utf-8")
            with self.assertRaises(MODULE.PluginError) as error:
                MODULE.json_tables(path)

        self.assertEqual(error.exception.code, "INPUT_INVALID")
        self.assertIn("options", str(error.exception))


if __name__ == "__main__":
    unittest.main()
