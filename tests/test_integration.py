from __future__ import annotations

import json, shutil, tempfile, unittest
from unittest.mock import patch
from datetime import date, timedelta
from pathlib import Path

from testbox.core.plugin_packages import install_plugin, package_plugin, uninstall_plugin
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
        _, result = self.runtime.run("sql.parse", {"input": str(sql), "format": "json"})
        self.assertEqual(result.status, "success"); self.assertEqual(result.data["field_count"], 2)
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
        timed_runtime = Runtime(self.temp, timeout_seconds=0.01)
        with patch("testbox.core.runtime.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired(["host"], 0.01)):
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
