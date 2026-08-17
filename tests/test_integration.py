from __future__ import annotations

import importlib.util, json, shutil, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

from testbox.core.plugin_packages import install_plugin, package_plugin, uninstall_plugin
from testbox.core.manifest import Manifest
from testbox.core.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]

class RuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp()); shutil.copytree(ROOT / "plugins", self.temp / "plugins")
        self.runtime = Runtime(self.temp)
    def tearDown(self):
        self.runtime.close()
        shutil.rmtree(self.temp)
    def test_discovers_two_p0_commands(self): self.assertEqual(set(self.runtime.manager.available), {"data.mock", "sql.parse"})
    def test_mock_is_repeatable_and_traced(self):
        first_id, first = self.runtime.run("data.mock", {"count": 2, "format": "json", "seed": 7})
        second_id, second = self.runtime.run("data.mock", {"count": 2, "format": "json", "seed": 7})
        self.assertEqual(first.status, "success"); self.assertEqual(second.status, "success")
        first_data = (self.temp / "workspace" / first_id / "output" / first.files[0]).read_text()
        second_data = (self.temp / "workspace" / second_id / "output" / second.files[0]).read_text()
        self.assertEqual(first_data, second_data); self.assertTrue((self.temp / "workspace" / first_id / "result.json").exists()); self.assertTrue((self.temp / "workspace" / first_id / "report.md").exists())
    def test_sql_parser(self):
        sql = self.temp / "schema.sql"; sql.write_text("CREATE TABLE users (id INT NOT NULL COMMENT 'ID', name VARCHAR(50) COMMENT '姓名');", encoding="utf-8")
        task_id, result = self.runtime.run("sql.parse", {"input": str(sql), "format": "json"})
        self.assertEqual(result.status, "success"); self.assertEqual(result.data["field_count"], 2)
        manifest = json.loads((self.temp / "workspace" / task_id / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["inputs"][0]["parameter"], "input")
        self.assertTrue((self.temp / "workspace" / task_id / manifest["inputs"][0]["staged_path"]).is_file())
        self.assertEqual(manifest["inputs"][0]["sha256"], __import__("hashlib").sha256(sql.read_bytes()).hexdigest())
    def test_sql_parser_exports_xlsx(self):
        sql = self.temp / "schema.sql"; sql.write_text("CREATE TABLE users (id INT NOT NULL);", encoding="utf-8")
        task_id, result = self.runtime.run("sql.parse", {"input": str(sql), "format": "xlsx"})
        self.assertEqual(result.status, "success")
        output = self.temp / "workspace" / task_id / "output" / result.files[0]
        with __import__("zipfile").ZipFile(output) as archive:
            self.assertIn("xl/worksheets/sheet1.xml", archive.namelist())
    def test_sql_parser_supports_nested_types_constraints_and_postgres_comments(self):
        sql = self.temp / "mixed.sql"
        sql.write_text("""CREATE TABLE public.orders (
            id BIGINT GENERATED ALWAYS AS IDENTITY,
            customer_id UUID NOT NULL,
            amount DECIMAL(18,2) DEFAULT 0.00,
            metadata JSONB,
            CONSTRAINT orders_pk PRIMARY KEY (id),
            CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customer(id)
        );
        COMMENT ON TABLE public.orders IS '订单表';
        COMMENT ON COLUMN public.orders.amount IS '交易金额';
        """, encoding="utf-8")
        task_id, result = self.runtime.run("sql.parse", {"input": str(sql), "format": "json", "dialect": "postgresql"})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        by_name = {row["field"]: row for row in rows}
        self.assertEqual(by_name["amount"]["type"], "DECIMAL(18,2)")
        self.assertEqual(by_name["amount"]["comment"], "交易金额")
        self.assertTrue(by_name["id"]["primary_key"])
        self.assertEqual(by_name["customer_id"]["foreign_table"], "customer")
        self.assertTrue(by_name["id"]["auto_increment"])
    def test_sql_parser_supports_sqlserver_and_fail_on_warnings(self):
        sql = self.temp / "sqlserver.sql"
        sql.write_text("CREATE TABLE [dbo].[users] ([id] INT IDENTITY(1,1) NOT NULL PRIMARY KEY, [name] NVARCHAR(100) NULL);", encoding="utf-8")
        task_id, result = self.runtime.run("sql.parse", {"input": str(sql), "format": "json", "dialect": "sqlserver"})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["table"], "dbo.users")
        self.assertTrue(rows[0]["auto_increment"])
    def test_manifest_requires_minimum_capabilities(self):
        plugin = self.temp / "invalid-plugin"; plugin.mkdir()
        (plugin / "manifest.yaml").write_text("""schema_version: 1
name: invalid-plugin
version: 1.0.0
description: invalid
category: test
core_compatibility: \">=1.0,<2.0\"
entry: src.main:Plugin
commands: []
""", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "capabilities"):
            Manifest.load(plugin / "manifest.yaml")
    def test_manifest_rejects_incompatible_core_version(self):
        plugin = self.temp / "incompatible-plugin"; plugin.mkdir()
        (plugin / "manifest.yaml").write_text("""schema_version: 1
name: incompatible-plugin
version: 1.0.0
description: invalid
category: test
core_compatibility: \">=2.0,<3.0\"
entry: src.main:Plugin
commands:
  - name: test.run
    description: test
capabilities:
  concurrency: true
  network: false
  filesystem: output-only
  resources: []
""", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不兼容"):
            Manifest.load(plugin / "manifest.yaml")
    def test_project_config_is_injected_into_plugin(self):
        (self.temp / "config.yaml").write_text('phone_prefixes: "199"\n', encoding="utf-8")
        task_id, result = self.runtime.run("data.mock", {"count": 1, "format": "json", "seed": 7})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        self.assertTrue(rows[0]["phone"].startswith("199"))
    def test_schema_maximum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能大于"):
            self.runtime.run("data.mock", {"count": 100001, "format": "json"})
    def test_data_generator_supports_field_unique_phone_rule(self):
        rules = [{"name": "mobile", "generator": "mobile_cn", "unique": True, "options": {"prefixes": ["199"]}}]
        task_id, result = self.runtime.run("data.mock", {"count": 100, "format": "json", "seed": 7, "rules": rules})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        self.assertEqual(len({row["mobile"] for row in rows}), 100)
        self.assertTrue(all(row["mobile"].startswith("199") and len(row["mobile"]) == 11 for row in rows))
        self.assertEqual(result.data["unique_fields"], ["mobile"])
        self.assertEqual(result.data["administrative_divisions_version"], "2025-12-27-c49d495")
    def test_data_generator_rejects_insufficient_unique_capacity(self):
        rules = [{"name": "status", "generator": "weighted_enum", "unique": True, "options": {"values": ["A", "B"]}}]
        _, result = self.runtime.run("data.mock", {"count": 3, "format": "json", "rules": rules})
        self.assertEqual(result.status, "failed")
        self.assertIn("唯一值容量", result.message)
    def test_data_generator_filters_addresses_by_province_and_city(self):
        rules = [{"name": "address", "generator": "china_address", "options": {"province": ["广东省"], "city": ["深圳市"], "include_virtual_street": False}}]
        task_id, result = self.runtime.run("data.mock", {"count": 3, "format": "json", "seed": 7, "rules": rules})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        self.assertTrue(all(row["address"].startswith("广东省深圳市") for row in rows))
    def test_data_generator_filters_addresses_by_district_and_supports_hong_kong(self):
        mainland = [{"name": "address", "generator": "china_address", "options": {"province": ["广东省"], "city": ["深圳市"], "district": ["南山区"], "street_mode": "none"}}]
        task_id, result = self.runtime.run("data.mock", {"count": 1, "format": "json", "seed": 7, "rules": mainland})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["address"], "广东省深圳市南山区")
        hong_kong = [{"name": "address", "generator": "china_address", "options": {"province": ["香港特别行政区"], "city": ["香港岛"], "district": ["湾仔区"], "street_mode": "none"}}]
        task_id, result = self.runtime.run("data.mock", {"count": 1, "format": "json", "seed": 7, "rules": hong_kong})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["address"], "香港特别行政区香港岛湾仔区")
    def test_data_generator_supports_nullable_rate(self):
        rules = [{"name": "optional_text", "generator": "template", "nullable_rate": 1, "options": {"value": "value"}}]
        task_id, result = self.runtime.run("data.mock", {"count": 2, "format": "json", "seed": 7, "rules": rules})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        self.assertEqual([row["optional_text"] for row in rows], [None, None])
    def test_data_generator_financial_customer_template(self):
        task_id, result = self.runtime.run("data.mock", {"count": 2, "format": "json", "seed": 7, "template": "retail_customer"})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["customer_id"], "TEST-CUST-00000001")
        self.assertIn("risk_level", rows[0])
        self.assertIn("registered_address", rows[0])
    def test_data_generator_infers_rules_from_sql_ddl(self):
        ddl = self.temp / "customer.sql"
        ddl.write_text("CREATE TABLE customer (customer_id VARCHAR(20) COMMENT '客户ID', mobile VARCHAR(11) COMMENT '手机号', amount DECIMAL(18,2) COMMENT '金额');", encoding="utf-8")
        task_id, result = self.runtime.run("data.mock", {"count": 2, "format": "json", "seed": 7, "source_file": str(ddl), "source_format": "sql"})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["customer_id"], "TEST-CUST-00000001")
        self.assertEqual(len(rows[0]["mobile"]), 11)
        self.assertIsInstance(rows[0]["amount"], float)
    def test_data_generator_infers_rules_from_excel_field_list(self):
        source = ROOT / "plugins" / "data-generator" / "src" / "main.py"
        spec = importlib.util.spec_from_file_location("data_generator_test", source)
        module = importlib.util.module_from_spec(spec); self.assertIsNotNone(spec.loader); spec.loader.exec_module(module)
        field_list = self.temp / "fields.xlsx"
        field_list.write_bytes(module.xlsx([{"field": "mobile", "type": "VARCHAR(11)", "comment": "手机号"}, {"field": "risk_level", "type": "VARCHAR(10)", "comment": "风险等级"}]))
        task_id, result = self.runtime.run("data.mock", {"count": 2, "format": "json", "seed": 7, "source_file": str(field_list), "source_format": "excel"})
        rows = json.loads((self.temp / "workspace" / task_id / "output" / result.files[0]).read_text(encoding="utf-8"))
        self.assertEqual(len(rows[0]["mobile"]), 11)
        self.assertIn(rows[0]["risk_level"], {"低", "中", "高"})
    def test_unknown_parameter_is_rejected(self):
        with self.assertRaises(ValueError): self.runtime.run("data.mock", {"count": 1, "format": "json", "oops": True})
    def test_task_history_and_workspace_clean(self):
        task_id, _ = self.runtime.run("data.mock", {"count": 1, "format": "json", "seed": 7})
        record = self.runtime.history.get(task_id)
        self.assertEqual(record["status"], "SUCCEEDED")
        self.assertEqual(record["params"]["seed"], 7)
        self.assertEqual(self.runtime.clean_workspace(date.today() + timedelta(days=1)), 1)
    def test_sensitive_params_are_redacted(self):
        self.assertEqual(Runtime._redact_params({"api_token": "private", "count": 1}), {"api_token": "***", "count": 1})
    def test_host_timeout_is_recorded(self):
        timed_runtime = Runtime(self.temp, timeout_seconds=0)
        task_id, result = timed_runtime.run("data.mock", {"count": 1, "format": "json"})
        timed_runtime.close()
        self.assertEqual(result.data["error_code"], "TIMEOUT")
        self.assertEqual(self.runtime.history.get(task_id)["status"], "FAILED")
    def test_incomplete_tasks_are_abandoned_at_startup(self):
        self.runtime.history.create({"id": "20200101T000000-deadbeef", "plugin_name": "data-generator", "plugin_version": "1.0.0", "command": "data.mock", "params": {}, "started_at": "2020-01-01T00:00:00+00:00", "result_path": "missing", "workspace_path": "missing", "host_pid": None})
        recovered = Runtime(self.temp)
        self.assertEqual(recovered.history.get("20200101T000000-deadbeef")["status"], "ABANDONED")
        recovered.close()
    def test_plugin_archive_round_trip(self):
        archive = self.temp / "data-generator.zip"
        package_plugin(self.temp / "plugins" / "data-generator", archive)
        uninstall_plugin("data-generator", self.temp / "plugins")
        after_uninstall = Runtime(self.temp)
        self.assertNotIn("data.mock", after_uninstall.manager.available)
        after_uninstall.close()
        manifest = install_plugin(archive, self.temp / "plugins")
        self.assertEqual(manifest.name, "data-generator")
        after_install = Runtime(self.temp)
        self.assertIn("data.mock", after_install.manager.available)
        after_install.close()

if __name__ == "__main__": unittest.main()
