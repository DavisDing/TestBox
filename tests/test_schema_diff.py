"""Real Runtime -> subprocess Host tests, isolated roots, no other plugins."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from testbox.core.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]
DDL = "CREATE TABLE demo (id INT NOT NULL PRIMARY KEY, name VARCHAR(12), amount DECIMAL(8,2) DEFAULT 0);"


class SchemaDiffHostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        shutil.copytree(ROOT / "plugins" / "schema-diff", self.root / "plugins" / "schema-diff",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.runtime = Runtime(self.root)

    def tearDown(self):
        self.runtime.close()
        self.temporary.cleanup()

    def run_command(self, command, params, status="success"):
        task_id, result = self.runtime.run(command, params)
        self.assertEqual(result.status, status, (result.message, result.data))
        self.assertEqual(self.runtime.get_task(task_id)["status"], "SUCCEEDED" if status == "success" else "FAILED")
        if status == "success":
            self.assertEqual(result.files, [task_id + ".json", task_id + ".md"])
            output = Path(self.runtime.get_task(task_id)["workspace_path"]) / "output"
            report = json.loads((output / (task_id + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(report["task_id"], task_id)
            self.assertIn(task_id, (output / (task_id + ".md")).read_text(encoding="utf-8"))
            return result, report
        return result, None

    def diff(self, left=DDL, right=DDL, **params):
        return self.run_command("sql.diff", {"left_text": left, "right_text": right, **params})

    def path(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8", newline="")
        return str(path)

    def test_discovery_and_preview_metadata(self):
        self.assertEqual(set(self.runtime.manager.available), {"sql.diff", "sql.preview"})
        schema = self.runtime.get_command_schema("sql.diff")
        self.assertEqual(schema["x-preview"]["command"], "sql.preview")
        self.assertEqual(schema["x-preview"]["sql_column"], "sql_column")
        self.assertEqual(schema["x-preview"]["sources"][0], {"input": "left", "inputs": "left_inputs", "text": "left_text", "options": "left_options", "mode": "left_mode", "label": "左侧"})

    def test_identical_ddl_equal_and_type_change_different(self):
        result, report = self.diff()
        self.assertEqual(result.data["verdict"], "equal")
        self.assertTrue(result.data["complete"])
        self.assertEqual(report["unknown"], [])
        result, report = self.diff(right=DDL.replace("VARCHAR(12)", "VARCHAR(14)"))
        self.assertEqual(result.data["verdict"], "different")
        self.assertTrue(any(d.get("attribute") == "length" for d in report["differences"]))
        self.assertTrue(report["differences"][0]["left_location"]["sql_line"])

    def test_select_vs_ddl_known_columns_no_fabricated_declarations(self):
        result, report = self.diff(left="SELECT d.id, d.name, d.amount FROM demo AS d;")
        self.assertEqual(result.data["verdict"], "inconclusive")
        self.assertFalse(result.data["complete"])
        fields = report["left"]["statements"][0]["fields"]
        self.assertEqual([f["field"] for f in fields], ["id", "name", "amount"])
        self.assertEqual(fields[0]["source_field"], "d.id")
        self.assertIsNone(fields[0]["type"])
        self.assertIsNone(fields[0]["nullable"])
        self.assertFalse(fields[0]["declared"])
        self.assertEqual(report["differences"], [])
        result, _ = self.diff(left="SELECT id, absent FROM demo")
        self.assertEqual(result.data["verdict"], "different")

    def test_select_alias_expression_and_unknown_expression_types(self):
        sql = "SELECT id AS key_id, COALESCE(name, 'a,b;z') label, amount + 1 AS next_amount FROM demo"
        result, report = self.run_command("sql.preview", {"text": sql})
        self.assertEqual([r["field"] for r in result.data["rows"]], ["key_id", "label", "next_amount"])
        self.assertEqual(result.data["rows"][0]["alias"], "key_id")
        self.assertIn("a,b;z", result.data["rows"][1]["expression"])
        self.assertFalse(report["complete"])

    def test_insert_multiline_quoted_values_and_missing_columns(self):
        sql = "INSERT INTO demo (id,name,amount) VALUES (1,'a,b;\nc',2.2), (2,concat('x,y','z'),3.3);"
        result, report = self.diff(left=sql)
        self.assertEqual(result.data["verdict"], "inconclusive")
        self.assertEqual(report["left"]["statements"][0]["value_rows"], 2)
        self.assertTrue(all(f["type"] is None for f in report["left"]["statements"][0]["fields"]))
        result, report = self.diff(left="INSERT INTO demo VALUES (1,'x',2),(2,'y',3)")
        self.assertEqual(result.data["verdict"], "inconclusive")
        self.assertIn("INSERT_COLUMNS_UNKNOWN", [u["code"] for u in report["unknown"]])
        self.run_command("sql.diff", {"left_text": "INSERT INTO demo(id,name) VALUES(1,'x'),(2)", "right_text": DDL}, "failed")

    def test_select_star_is_not_equal_even_against_itself(self):
        for sql in ("SELECT * FROM demo", "SELECT d.* FROM demo d"):
            with self.subTest(sql=sql):
                result, report = self.diff(left=sql, right=sql)
                self.assertEqual(result.data["verdict"], "inconclusive")
                self.assertFalse(report["complete"])
                self.assertIn("WILDCARD", [u["code"] for u in report["unknown"]])
        result, _ = self.diff(left="SELECT * FROM demo")
        self.assertEqual(result.data["verdict"], "inconclusive")

    def test_unsupported_never_disappears_or_false_equal(self):
        additions = ["ALTER TABLE demo ADD extra INT", "DROP TABLE demo", "UPDATE demo SET id=1",
                     "WITH x AS (SELECT id FROM demo) SELECT id FROM x",
                     "SELECT id FROM demo UNION SELECT id FROM demo",
                     "SELECT a.id FROM demo a JOIN other b ON a.id=b.id",
                     "SELECT id FROM (SELECT id FROM demo) d",
                     "SELECT (SELECT id FROM demo) AS nested FROM demo"]
        for addition in additions:
            with self.subTest(sql=addition):
                text = DDL + "\n" + addition + ";"
                result, report = self.diff(left=text, right=text)
                self.assertEqual(result.data["verdict"], "inconclusive")
                self.assertEqual(len(report["left"]["statements"]), 2)
                self.assertTrue(report["left"]["statements"][1]["unsupported"])
        result, _ = self.diff(left=DDL + " ALTER TABLE demo ADD x INT;")
        self.assertEqual(result.data["verdict"], "inconclusive")

    def test_quote_comment_parentheses_safe_ddl_and_schema(self):
        sql = '''-- comma , ; CREATE ignored
CREATE TABLE "s"."odd,table" (
 "id" INT NOT NULL,
 [note,field] VARCHAR(20) DEFAULT 'a,b;\nc''d' COMMENT 'NOT NULL, PRIMARY KEY',
 `sum` DECIMAL(12,3) DEFAULT (1 + 2),
 /* fake ,); CREATE TABLE */ PRIMARY KEY ("id")
);'''
        result, report = self.diff(left=sql, right=sql)
        self.assertEqual(result.data["verdict"], "equal")
        rows = report["left"]["statements"][0]["fields"]
        self.assertEqual(rows[0]["table"], "s.odd,table")
        self.assertEqual(rows[0]["schema"], "s")
        self.assertEqual(rows[1]["field"], "note,field")
        self.assertTrue(rows[1]["nullable"])
        self.assertFalse(rows[1]["primary_key"])
        self.assertIn("a,b;\nc''d", rows[1]["default"])
        self.assertEqual(rows[2]["precision"], 12)
        self.assertEqual(rows[2]["scale"], 3)
        self.assertEqual(rows[0]["location"]["sql_line"], 3)

    def test_hard_parse_errors_failed(self):
        for text in ("CREATE TABLE x (id INT", "CREATE TABLE x (id INT,)", "CREATE TABLE x (id INT DEFAULT)",
                     "CREATE TABLE x (id INT, id VARCHAR(1))", "SELECT 'bad FROM demo", "SELECT id, FROM demo",
                     "INSERT INTO demo(id) VALUES()", "CREATE TABLE x (id INT); /* unclosed"):
            with self.subTest(sql=text):
                result, _ = self.run_command("sql.diff", {"left_text": text, "right_text": DDL}, "failed")
                self.assertEqual(result.data["error_code"], "SQL_PARSE_ERROR")

    def test_both_sides_exactly_one_checked_in_host(self):
        path = self.path("ddl.sql", DDL)
        cases = [{"right_text": DDL}, {"left_text": DDL}, {"left": path, "left_text": DDL, "right_text": DDL},
                 {"left_text": DDL, "right": path, "right_text": DDL}, {"left_text": "", "right_text": DDL}]
        for params in cases:
            with self.subTest(params=list(params)):
                result, _ = self.run_command("sql.diff", params, "failed")
                self.assertEqual(result.data["error_code"], "INVALID_PARAMS")

    def test_preview_text_file_and_display_truncation(self):
        for params in ({"text": DDL}, {"input": self.path("ddl.sql", DDL)}):
            result, report = self.run_command("sql.preview", {**params, "sample_rows": 1})
            self.assertEqual(len(result.data["rows"]), 1)
            self.assertTrue(result.data["sample_truncated"])
            self.assertTrue(result.data["complete"])
            self.assertEqual(result.data["total_rows"], 3)
            self.assertEqual(len(report["structure"]["statements"][0]["fields"]), 3)
            self.assertIn("location", result.data["columns"])
        result, _ = self.run_command("sql.preview", {"text": "SELECT * FROM demo", "sample_rows": 0})
        self.assertFalse(result.data["complete"])
        self.assertEqual(result.data["rows"], [])
        self.run_command("sql.preview", {}, "failed")
        self.run_command("sql.preview", {"input": self.path("both.sql", DDL), "text": DDL}, "failed")

    def test_sql_txt_requires_explicit_format(self):
        path = self.path("ddl.txt", DDL)
        result, _ = self.run_command("sql.preview", {"input": path, "options": {"format": "sql"}})
        self.assertEqual(result.data["total_rows"], 3)
        self.run_command("sql.preview", {"input": path}, "failed")

    def declared_rows(self):
        _, report = self.diff()
        # Exact old sql.parse row shape; no internal kind/known metadata.
        return [{key: value for key, value in row.items() if key in {"table", "field", "type", "length", "precision", "scale", "nullable", "default", "primary_key", "unique", "auto_increment", "comment", "foreign_table", "foreign_field"}}
                for row in report["left"]["statements"][0]["fields"]]

    def test_structure_csv_with_ddl_and_partial_declarations(self):
        rows = self.declared_rows()
        path = self.root / "structure.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        result, report = self.run_command("sql.diff", {"left": str(path), "left_mode": "structure", "right_text": DDL})
        self.assertEqual(result.data["verdict"], "equal")
        self.assertEqual(report["left"]["statements"][0]["fields"][0]["location"]["row"], 2)
        partial = self.path("partial.json", json.dumps([{"table": "demo", "field": "id", "type": "INT"}]))
        result, _ = self.run_command("sql.diff", {"left": partial, "left_mode": "structure", "right_text": "CREATE TABLE demo(id INT)"})
        self.assertEqual(result.data["verdict"], "inconclusive")
        ordinary = self.path("ordinary.csv", "id,name\n1,test\n")
        self.run_command("sql.preview", {"input": ordinary, "input_mode": "structure"}, "failed")
        self.run_command("sql.preview", {"input": str(path)}, "failed")
        self.run_command("sql.preview", {"text": DDL, "input_mode": "structure"}, "failed")

    def test_all_table_carriers_and_sql_column(self):
        data = [{"query": "SELECT id FROM demo"}, {"query": "INSERT INTO demo(id) VALUES(1),(2)"}]
        paths = []
        for fmt, delimiter in (("csv", ","), ("tsv", "\t"), ("txt", "|")):
            path = self.root / ("queries." + fmt)
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, ["query"], delimiter=delimiter)
                writer.writeheader()
                writer.writerows(data)
            paths.append((path, {"delimiter": delimiter}))
        paths.append((Path(self.path("queries.json", json.dumps({"payload": {"queries": data}}))), {"json_path": "payload.queries"}))
        paths.append((Path(self.path("queries.jsonl", "\n".join(json.dumps(row) for row in data))), {}))
        from openpyxl import Workbook
        for fmt in ("xlsx", "xlsm"):
            path = self.root / ("queries." + fmt)
            book = Workbook()
            sheet = book.active
            sheet.title = "Queries"
            sheet.append(["preamble"])
            sheet.append(["query"])
            for row in data:
                sheet.append([row["query"]])
            book.save(path)
            book.close()
            paths.append((path, {"sheet": "Queries", "header_row": 2, "start_row": 3}))
        for path, options in paths:
            with self.subTest(format=path.suffix):
                result, report = self.run_command("sql.preview", {"input": str(path), "sql_column": "query", "options": options})
                self.assertEqual(result.data["total_rows"], 2)
                self.assertEqual(len(report["structure"]["statements"]), 2)
                self.assertFalse(result.data["complete"])

    def test_json_object_path_and_structure_formats(self):
        rows = self.declared_rows()
        from openpyxl import Workbook
        paths = []
        for fmt, delimiter in (("tsv", "\t"), ("txt", "||")):
            # Safe synthetic declarations contain no delimiter/newline in cells.
            path = self.path("structure." + fmt, delimiter.join(rows[0]) + "\n" + "\n".join(delimiter.join("" if v is None else str(v) for v in row.values()) for row in rows))
            paths.append((path, {"delimiter": delimiter}))
        paths.append((self.path("structure.json", json.dumps({"fields": rows})), {"json_path": "fields"}))
        paths.append((self.path("structure.jsonl", "\n".join(json.dumps(row) for row in rows)), {}))
        for fmt in ("xlsx", "xlsm"):
            path = self.root / ("structure." + fmt)
            book = Workbook()
            book.active.append(list(rows[0]))
            for row in rows:
                book.active.append(list(row.values()))
            book.save(path)
            book.close()
            paths.append((str(path), {}))
        for path, options in paths:
            with self.subTest(path=path):
                result, _ = self.run_command("sql.diff", {"left": path, "left_mode": "structure", "left_options": options, "right_text": DDL})
                self.assertEqual(result.data["verdict"], "equal")
        single = self.path("one.json", json.dumps(rows[0]))
        result, _ = self.run_command("sql.preview", {"input": single, "input_mode": "structure"})
        self.assertEqual(result.data["total_rows"], 1)

    def test_sdk_invalid_and_limits_are_not_silent_sampling(self):
        bad_json = ['[{"sql":"SELECT id FROM demo","sql":"SELECT name FROM demo"}]', '[{"sql":NaN}]', '[]']
        for text in bad_json:
            path = self.path("bad.json", text)
            self.run_command("sql.preview", {"input": path}, "failed")
        path = self.path("many.json", json.dumps([{"sql": DDL}, {"sql": DDL}]))
        self.run_command("sql.preview", {"input": path, "options": {"max_rows": 1}}, "failed")
        self.run_command("sql.preview", {"text": DDL, "options": {"max_rows": 1}}, "failed")
        path = self.path("missing.csv", "wrong\nvalue\n")
        self.run_command("sql.preview", {"input": path}, "failed")

    def test_unsupported_ddl_and_special_quotes_do_not_report_equal(self):
        for sql in ("CREATE TABLE demo(id INT CHECK(id>0))", "CREATE TABLE demo(id INT) ENGINE=InnoDB",
                    "CREATE TABLE demo(id custom_type)", "CREATE TABLE demo(id INT, name INT, UNIQUE(id,name))",
                    "CREATE TABLE demo(id INT DEFAULT $$a;b,c$$)", "/*! CREATE TABLE demo(id INT) */",
                    "CREATE TABLE demo(id VARCHAR(3) DEFAULT 'a\\\'b')"):
            with self.subTest(sql=sql):
                result, _ = self.diff(left=sql, right=sql)
                self.assertEqual(result.data["verdict"], "inconclusive")

    def test_many_unknowns_bounded_only_in_result_full_in_artifact(self):
        sql = ";".join("SELECT * FROM demo" for _ in range(120))
        result, report = self.run_command("sql.preview", {"text": sql, "sample_rows": 100})
        self.assertEqual(len(result.data["rows"]), 100)
        self.assertTrue(result.data["sample_truncated"])
        self.assertTrue(result.data["unknown_truncated"])
        self.assertEqual(len(report["structure"]["statements"]), 120)
        self.assertFalse(report["complete"])


    def test_simple_gui_contract_create_preview_returns_two_rows(self):
        result, _ = self.run_command("sql.preview", {"text": "CREATE TABLE t (id INTEGER,name VARCHAR(10));", "options": {"format": "auto"}})
        self.assertEqual([(r["field"], r["type"]) for r in result.data["rows"]], [("id", "INTEGER"), ("name", "VARCHAR(10)")])
        self.assertTrue(result.data["complete"])

    def test_expression_sources_and_same_select_still_inconclusive(self):
        sql = "SELECT amount + id AS total, COALESCE(name, 'x') AS label, COUNT(*) AS n FROM demo"
        result, _ = self.run_command("sql.preview", {"text": sql})
        self.assertEqual(result.data["rows"][0]["source_fields"], ["amount", "id"])
        self.assertEqual(result.data["rows"][1]["source_fields"], ["name"])
        self.assertEqual(result.data["rows"][2]["source_fields"], [])
        result, _ = self.diff(left=sql, right=sql)
        self.assertEqual(result.data["verdict"], "inconclusive")
        result, report = self.diff(left="SELECT amount + 1 AS total FROM demo", right="SELECT amount + 2 AS total FROM demo")
        self.assertEqual(result.data["verdict"], "different")
        self.assertIn("PROJECTION", [d["code"] for d in report["differences"]])

    def test_no_false_equal_on_unrecognized_expression_or_missing_comma(self):
        for sql in ("CREATE TABLE t(id INT DEFAULT 0 name VARCHAR(3))",
                    "CREATE TABLE t(id INT DEFAULT 1 COLLATE strange)",
                    "SELECT id nonsense garbage FROM demo", "SELECT CASE WHEN id=1 THEN name END AS x FROM demo",
                    "SELECT CAST(id AS VARCHAR(10)) AS x FROM demo"):
            with self.subTest(sql=sql):
                result, _ = self.diff(left=sql, right=sql)
                self.assertEqual(result.data["verdict"], "inconclusive")
        self.run_command("sql.preview", {"text": "CREATE TABLE t(id INT DEFAULT (1+))"}, "failed")

    def test_composite_primary_order_and_legacy_unknown(self):
        left = "CREATE TABLE t(a INT,b INT,PRIMARY KEY(a,b))"
        right = "CREATE TABLE t(a INT,b INT,PRIMARY KEY(b,a))"
        result, report = self.diff(left=left, right=right)
        self.assertEqual(result.data["verdict"], "different")
        self.assertIn("PRIMARY_KEY_ORDER", [d["code"] for d in report["differences"]])
        result, _ = self.diff(left=left, right=left)
        self.assertEqual(result.data["verdict"], "equal")
        _, report = self.diff(left=left, right=left)
        rows = [{k:v for k,v in row.items() if k in {"table","field","type","length","precision","scale","nullable","default","primary_key","unique","auto_increment","comment","foreign_table","foreign_field"}} for row in report["left"]["statements"][0]["fields"]]
        path = self.path("composite.json", json.dumps(rows))
        result, _ = self.run_command("sql.diff", {"left":path,"left_mode":"structure","right_text":left})
        self.assertEqual(result.data["verdict"], "inconclusive")

    def test_quoted_dot_identifier_is_not_schema_qualification(self):
        result, report = self.diff(left='CREATE TABLE "s.t"(id INT)', right='CREATE TABLE "s"."t"(id INT)')
        self.assertEqual(result.data["verdict"], "different")
        self.assertEqual(report["left"]["statements"][0]["fields"][0]["schema"], "")
        self.assertEqual(report["right"]["statements"][0]["fields"][0]["schema"], "s")

    def test_constraints_defaults_dimensions_and_order(self):
        sql = "CREATE TABLE t(a INT NOT NULL UNIQUE AUTO_INCREMENT, b VARCHAR(12) DEFAULT 'NULL,PRIMARY KEY', c DECIMAL(9,2) NULL DEFAULT NULL)"
        result, report = self.diff(left=sql, right=sql)
        self.assertEqual(result.data["verdict"], "equal")
        a,b,c = report["left"]["statements"][0]["fields"]
        self.assertTrue(a["auto_increment"])
        self.assertTrue(a["unique"])
        self.assertFalse(a["nullable"])
        self.assertFalse(b["primary_key"])
        self.assertEqual(c["default"], "NULL")
        for replacement in (sql.replace("NOT NULL", "NULL"), sql.replace("UNIQUE ", ""), sql.replace("DEFAULT NULL", "DEFAULT 1"), sql.replace("DECIMAL(9,2)", "DECIMAL(9,3)")):
            result, _ = self.diff(left=sql,right=replacement)
            self.assertEqual(result.data["verdict"], "different")

    def test_errors_keep_source_locations_and_empty_text(self):
        path = self.path("broken.sql", "CREATE TABLE t(id)")
        result, _ = self.run_command("sql.preview", {"input":path}, "failed")
        self.assertTrue(result.data["details"]["location"]["source"].endswith("broken.sql"))
        self.run_command("sql.preview", {"text":""}, "failed")
        self.run_command("sql.preview", {"text":";; -- no statements"}, "failed")

    def test_structure_rejects_contradictory_dimensions_and_boolean(self):
        rows = self.declared_rows()
        rows[1]["length"] = 99
        path = self.path("conflict.json", json.dumps(rows))
        self.run_command("sql.preview", {"input":path,"input_mode":"structure"}, "failed")
        rows = self.declared_rows()
        rows[0]["nullable"] = "perhaps"
        path = self.path("invalid_boolean.json", json.dumps(rows))
        self.run_command("sql.preview", {"input":path,"input_mode":"structure"}, "failed")


    def test_quoted_field_dots_and_qualified_implicit_alias(self):
        result, _ = self.run_command("sql.preview", {"text": 'SELECT "a.b", d.id ident FROM "s"."t" d'})
        self.assertEqual([r["field"] for r in result.data["rows"]], ["a.b", "ident"])
        self.assertEqual(result.data["rows"][1]["source_field"], "d.id")
        self.assertEqual(result.data["rows"][1]["alias"], "ident")

    def test_structure_location_sheet_and_sql_cells_with_newlines(self):
        import io
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["sql"])
        writer.writerow(["CREATE TABLE t(\n id INT,\n name VARCHAR(10) DEFAULT 'a,b;z'\n);"])
        path = self.path("multiline.csv", stream.getvalue())
        result, report = self.run_command("sql.preview", {"input":path})
        self.assertTrue(result.data["complete"])
        self.assertEqual(result.data["total_rows"], 2)
        row = report["structure"]["statements"][0]["fields"][1]
        self.assertEqual(row["location"]["row"], 2)
        self.assertEqual(row["location"]["sql_line"], 3)


if __name__ == "__main__":
    unittest.main()
