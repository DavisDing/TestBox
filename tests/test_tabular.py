from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest

from testbox.sdk import PluginError, normalize_dataset, read_dataset


class TabularTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def file(self, name, text):
        path = self.root / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def test_quoted_multiline_and_csv_escaped_delimiter(self):
        p = self.file("a.csv", 'id,value\r\n001,"a,b\nline"\r\n002,"a""b"\r\n')
        before = p.read_bytes()
        data = read_dataset(p)
        self.assertEqual(data["rows"], [{"id":"001","value":"a,b\nline"}, {"id":"002","value":'a"b'}])
        self.assertEqual([p["row"] for p in data["locations"]], [2, 4])
        self.assertEqual(p.read_bytes(), before)

    def test_custom_multichar_delimiters_and_record_separator(self):
        p = self.file("a.txt", 'id||value<EOR>001||"a||b<EOR>c"<EOR>')
        data = read_dataset(p, {"delimiter":"||", "record_separator":"<EOR>"})
        self.assertEqual(data["rows"], [{"id":"001","value":"a||b<EOR>c"}])
        self.assertEqual(data["locations"][0]["record"], 2)

    def test_encoding_and_explicit_tab_separator(self):
        p = self.root / "a.txt"
        p.write_bytes("编号\t名称\r\n001\t测试\r\n".encode("gb18030"))
        self.assertEqual(read_dataset(p, {"encoding":"gb18030","delimiter":r"\t", "record_separator":r"\r\n"})["rows"], [{"编号":"001","名称":"测试"}])
        with self.assertRaises(PluginError):
            read_dataset(p, {"delimiter":r"\t"})

    def test_json_path_and_missing_not_filled_with_null(self):
        p = self.file("a.json", '{"data":{"rows":[{"id":1,"v":null},{"id":2}]}}')
        data = read_dataset(p, {"json_path":"data.rows"})
        self.assertEqual(data["columns"], ["id","v"])
        self.assertNotIn("v", data["rows"][1])

    def test_json_rejects_duplicates_nonfinite_and_invalid_shape(self):
        for text in ('[{"x":1,"x":2}]','[{"x":NaN}]','[{"x":1e999}]','[1,2]', '[[{}]]', 'true'):
            with self.subTest(text=text), self.assertRaises(PluginError):
                read_dataset(self.file("a.json",text))

    def test_jsonl_locations_preserve_blank_lines(self):
        data = read_dataset(self.file("a.jsonl", '{"x":1}\n\n{"x":2}\n'))
        self.assertEqual([p["row"] for p in data["locations"]], [1,3])

    def test_bad_headers_width_quotes_and_unknown_options_fail(self):
        for text in ('id,id\n1,2\n','id,v\n1\n','id,v\n1,"not closed','id,v\n1,"x"oops\n'):
            with self.subTest(text=text), self.assertRaises(PluginError):
                read_dataset(self.file("a.csv", text))
        with self.assertRaises(PluginError):
            read_dataset(self.file("a.csv","id\n1"), {"delimeter":"|"})

    def test_limits_fail_not_truncate(self):
        p = self.file("a.csv","id\n1\n2\n")
        for options in ({"max_rows":1}, {"max_bytes":3}, {"max_cells":1}, {"max_cell_length":1}):
            if options == {"max_cell_length":1}:
                p = self.file("a.csv","id\n12\n")
            with self.subTest(options=options), self.assertRaises(PluginError):
                read_dataset(p,options)

    def test_no_header_explicit_columns_and_start_record(self):
        data = read_dataset(self.file("a.txt",'ignored|ignored\n001|A\n'), {"delimiter":"|","has_header":False,"columns":["id","v"],"start_row":2})
        self.assertEqual(data["rows"], [{"id":"001","v":"A"}])

    def test_normalization_independent_explicit_exact_decimal(self):
        original = read_dataset(self.file("a.json", '[{"编号":" 001 ","amount":"123456789012345678901234567890.1200","flag":"true"}]'))
        before = copy.deepcopy(original)
        result = normalize_dataset(original, {"trim":True,"column_mapping":{"编号":"id"},"types":{"amount":"decimal","flag":"boolean"}})
        self.assertEqual(result["rows"][0], {"id":"001","amount":"123456789012345678901234567890.12","flag":True})
        self.assertEqual(original,before)
        self.assertEqual(result["types"], {"amount":"decimal","flag":"boolean"})

    def test_normalization_collisions_and_conversion_errors(self):
        data = read_dataset(self.file("a.json", '[{"a":"1.5","b":true}]'))
        for rules in ({"column_mapping":{"a":"b"}},{"types":{"a":"integer"}},{"types":{"b":"integer"}},{"types":{"missing":"string"}},{"null_values":[None]}):
            with self.subTest(rules=rules), self.assertRaises(PluginError):
                normalize_dataset(data,rules)

    def test_excel_sheet_header_and_source_unchanged(self):
        from openpyxl import Workbook
        book = Workbook()
        sheet = book.active
        sheet.title = "Data"
        sheet.append(["说明",None])
        sheet.append(["id","value"])
        sheet.append(["001",12])
        path = self.root / "a.xlsx"
        book.save(path)
        before = path.read_bytes()
        data = read_dataset(path, {"sheet":"Data","header_row":2})
        self.assertEqual(data["rows"], [{"id":"001","value":12}])
        self.assertEqual(data["locations"][0]["row"],3)
        self.assertEqual(path.read_bytes(),before)
        with self.assertRaises(PluginError):
            read_dataset(path,{"sheet":"absent"})

    def test_preview_real_host_bounded_sample_and_full_row_count(self):
        import shutil
        from testbox.core.runtime import Runtime
        shutil.copytree(Path(__file__).resolve().parents[1] / "plugins" / "data-preview", self.root / "plugins" / "data-preview")
        p = self.file("a.csv","id\n001\n002\n")
        runtime = Runtime(self.root)
        self.addCleanup(runtime.close)
        task,result = runtime.run("data.preview", {"input":str(p),"sample_rows":1})
        self.assertEqual(result.status,"success",result.to_dict())
        self.assertEqual(result.data["row_count"],2)
        self.assertTrue(result.data["sample_truncated"])
        self.assertTrue(result.data["complete"])
        self.assertEqual(result.data["rows"],[{"id":"001"}])
        self.assertTrue(runtime.get_task_output_path(task,result.files[0]).is_file())

    def test_preview_large_cells_bound_transport_not_full_sample_artifact(self):
        import shutil
        from testbox.core.runtime import Runtime
        shutil.copytree(Path(__file__).resolve().parents[1] / "plugins" / "data-preview", self.root / "plugins" / "data-preview")
        raw = "文" * 5000
        p = self.file("large.json",json.dumps([{"value":raw}],ensure_ascii=False))
        runtime = Runtime(self.root)
        self.addCleanup(runtime.close)
        task,result=runtime.run("data.preview",{"input":str(p)})
        self.assertEqual(result.status,"success",result.to_dict())
        self.assertTrue(result.data["display_bounded"])
        self.assertLess(len(result.data["rows"][0]["value"]),200)
        report=json.loads(runtime.get_task_output_path(task,result.files[0]).read_text())
        self.assertEqual(report["rows"][0]["value"],raw)

    def test_ddl_extension_is_sql_text_not_delimited_table(self):
        text = "CREATE TABLE t (id INTEGER);"
        data = read_dataset(self.file("a.ddl",text))
        self.assertEqual(data["format"],"sql")
        self.assertEqual(data["rows"],[{"sql":text}])


if __name__ == "__main__":
    unittest.main()
