"""Real optional-Qt regressions; all Runtime writes stay in temporary roots."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

# Select the headless platform before importing Qt or creating QApplication.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PySide6 import QtCore, QtTest, QtWidgets
except ModuleNotFoundError as error:
    if error.name != "PySide6":
        raise
    QT_AVAILABLE = False
else:
    QT_AVAILABLE = True
    from testbox.gui import DataMockForm, DynamicSchemaForm, MainWindow


from testbox.core.schema_validator import SchemaValidationError, SchemaValidator

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {"data.mock", "sql.parse", "sql.select", "evidence.build", "data.preview", "data.compare", "data.check", "sql.diff", "sql.preview"}


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop dependency is not installed")
class GuiRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="testbox-gui-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(
            ROOT / "plugins", self.root / "plugins",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        # Never allow a modal message box to suspend a headless test. Assertions
        # still inspect every call; prompts are denied rather than confirmed.
        self.messages = {}
        for name in ("critical", "warning", "information", "question"):
            self.messages[name] = self.enterContext(patch.object(
                QtWidgets.QMessageBox, name,
                return_value=QtWidgets.QMessageBox.StandardButton.No,
            ))
        self.window = MainWindow(self.root)
        self.addCleanup(self._close_window)
        self.window.show()
        self.app.processEvents()

    def _close_window(self):
        # Let queued completion slots finish before closing SQLite/deleting the
        # TemporaryDirectory; never tear down underneath a live RuntimeWorker.
        finished = QtCore.QThreadPool.globalInstance().waitForDone(30_000)
        self.app.processEvents()
        if not finished:
            self.fail("GUI RuntimeWorker did not stop within 30 seconds")
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _wait_until(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertTrue(predicate(), "Timed out waiting for the GUI completion slot")

    def _assert_no_messages(self):
        for message in self.messages.values():
            message.assert_not_called()

    def test_schema_file_filters_override_defaults_and_array_item_hints(self):
        schema = {"type": "object", "properties": {
            "input": {"type": "string", "format": "file-path",
                      "x-file-filter": "自定义单文件 (*.sample)"},
            "attachments": {"type": "array", "x-file-filter": "自定义批量 (*.batch)",
                            "items": {"type": "string", "format": "file-path",
                                      "x-file-filter": "项目过滤 (*.item)"}},
        }}
        form = DynamicSchemaForm(schema, "example.run")
        self.addCleanup(form.deleteLater)
        self.assertEqual(form.fields["input"][1].filter_str, "自定义单文件 (*.sample)")
        self.assertEqual(form.fields["attachments"][1].filter_str, "自定义批量 (*.batch)")
        del schema["properties"]["attachments"]["x-file-filter"]
        item_form = DynamicSchemaForm(schema, "example.run")
        self.addCleanup(item_form.deleteLater)
        self.assertEqual(item_form.fields["attachments"][1].filter_str, "项目过滤 (*.item)")

    def test_generic_file_arrays_preserve_defaults_and_evidence_filters(self):
        schema = {"type": "object", "properties": {
            key: {"type": "array", "items": {"type": "string", "format": "file-path"}}
            for key in ("attachments", "screenshots", "existing_reports")
        }}
        source = self.root / "default.txt"
        source.write_text("TEST DATA ONLY", encoding="utf-8")
        default_paths = [str(source)]
        schema["properties"]["attachments"]["default"] = default_paths
        form = DynamicSchemaForm(schema, "example.run")
        self.addCleanup(form.deleteLater)
        self.assertEqual(form.fields["attachments"][1].filter_str, "所有文件 (*.*)")
        self.assertEqual(form.get_values()["attachments"], default_paths)
        for key in ("screenshots", "existing_reports"):
            self.assertIn("图片 / 报告文件", form.fields[key][1].filter_str)

    def test_catalog_history_and_diagnostics_navigation(self):
        self.assertEqual(self.window.runtime.root, self.root)
        self.assertEqual(self.window.runtime.workspace_dir, self.root / "workspace")
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self.assertEqual(
            {card[0] for card in self.window.page_catalog.all_cards}, COMMANDS,
        )
        self.window.nav_list.setCurrentRow(1)
        self.assertEqual(self.window.stack.currentIndex(), 4)
        self.assertEqual(self.window.page_history.table.rowCount(), 0)
        self.assertEqual(self.window.page_history.table_stack.currentIndex(), 2)
        self.window.nav_list.setCurrentRow(2)
        self.assertEqual(self.window.stack.currentIndex(), 5)
        diagnostics = self.window.page_diagnostics
        self.assertEqual(diagnostics.plugins_table.rowCount(), 8)
        self.assertEqual(diagnostics.unavailable_table.rowCount(), 0)
        self.assertEqual(diagnostics.available_stack.currentIndex(), 1)
        self.assertEqual(
            diagnostics.runtime_diagnostic_labels["workspace_dir"].text(),
            str(self.root / "workspace"),
        )
        self.window.nav_list.setCurrentRow(0)
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self._assert_no_messages()

    def test_all_four_command_forms_round_trip_presets(self):
        sql = self.root / "input.sql"
        sql.write_text("CREATE TABLE example (id INT);", encoding="utf-8")
        fields = self.root / "fields.json"
        fields.write_text("{}", encoding="utf-8")
        # Form-only fixture: this is deliberately NOT an executable Excel file.
        excel = self.root / "cases.xlsx"
        excel.write_bytes(b"form-only-path-fixture")
        cases = {
            "data.mock": {"count": 3, "format": "csv", "seed": 17},
            "sql.parse": {"input": str(sql), "format": "json", "dialect": "sqlite"},
            "sql.select": {"input": str(fields), "input_format": "json", "include_comments": True},
            "evidence.build": {"input": str(excel), "interactive": False, "update_excel": False},
        }
        for command, preset in cases.items():
            with self.subTest(command=command):
                self.window.navigate_to_command(command, preset)
                view = self.window.page_form
                self.assertEqual(self.window.stack.currentIndex(), 1)
                self.assertEqual(view.current_command, command)
                self.assertTrue(view.submit_btn.isEnabled())
                self.assertIsInstance(
                    view.form_widget, DataMockForm if command == "data.mock" else DynamicSchemaForm,
                )
                values = view.form_widget.get_values()
                for key, value in preset.items():
                    self.assertEqual(values[key], value)
        self._assert_no_messages()

    def test_missing_file_is_inline_validation_not_task_execution(self):
        self.window.navigate_to_command("sql.parse", {
            "input": str(self.root / "missing.sql"), "format": "json",
        })
        view = self.window.page_form
        submitted = QtTest.QSignalSpy(view.executeRequested)
        QtTest.QTest.mouseClick(view.submit_btn, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(submitted.count(), 0)
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.assertEqual(self.window.runtime.count_tasks(), 0)
        self.assertTrue(view.form_widget.error_labels["input"].text())
        self._assert_no_messages()

    def test_data_mock_async_submit_persisted_result_and_history(self):
        preset = {
            "count": 3, "format": "csv", "seed": 17,
            "fields": [{"name": "value", "type": "VARCHAR(16)", "generator": "template",
                        "options": {"value": "test-{index}"}}],
        }
        self.window.navigate_to_command("data.mock", preset)
        submitted = QtTest.QSignalSpy(self.window.page_form.executeRequested)
        QtTest.QTest.mouseClick(self.window.page_form.submit_btn, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(submitted.count(), 1)
        self.assertEqual(self.window.stack.currentIndex(), 2)
        self.assertEqual(self.window.page_running.lbl_cmd.text(), "data.mock")
        self._wait_until(lambda: self.window.stack.currentIndex() in {1, 3})
        self._assert_no_messages()
        self.assertEqual(self.window.stack.currentIndex(), 3)
        task_id = self.window.page_result.current_task_id
        record = self.window.runtime.get_task(task_id)
        result = self.window.runtime.get_task_result(task_id)
        self.assertEqual(record["status"], "SUCCEEDED")
        self.assertEqual(record["command"], "data.mock")
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["files"])
        # Resolve the recorded path rather than assuming a particular task layout.
        task_dir = Path(record["workspace_path"])
        self.assertTrue(task_dir.is_relative_to(self.root))
        for relative in result["files"]:
            output = task_dir / "output" / relative
            self.assertTrue(output.is_file())
            self.assertIn("test-", output.read_text(encoding="utf-8-sig"))
        self.assertEqual(self.window.page_result.current_task_info["status"], "SUCCEEDED")
        self.assertIn(task_id, self.window.page_result.title_task_id_lbl.text())
        self.window.switch_page(4)
        history = self.window.page_history
        self.assertEqual(history.table.rowCount(), 1)
        self.assertEqual(history.table.item(0, 0).text(), task_id)
        history.search_input.setText("no-such-task")
        self.assertEqual(history.table.rowCount(), 0)
        self.assertEqual(history.table_stack.currentIndex(), 2)
        history.search_input.clear()
        self.assertEqual(history.table.rowCount(), 1)
        history.table.cellDoubleClicked.emit(0, 0)
        self.assertEqual(self.window.stack.currentIndex(), 3)
        self.assertEqual(self.window.page_result.current_task_id, task_id)
        self._assert_no_messages()

    def test_open_non_finite_error_maps_to_existing_advanced_form_field(self):
        schema = {"type": "object", "properties": {
            "payload": {"type": "object", "additionalProperties": True, "ui:advanced": True},
        }}
        form = DynamicSchemaForm(schema)
        self.addCleanup(form.deleteLater)
        with self.assertRaises(SchemaValidationError) as caught:
            SchemaValidator().validate(schema, {"payload": {"weights": [math.inf]}})
        self.assertTrue(form.set_field_error(caught.exception.field, str(caught.exception)))
        self.assertFalse(form.error_labels["payload"].isHidden())
        self.assertIn("有限数值", form.error_labels["payload"].text())
        self._assert_no_messages()

    def test_sensitive_summary_redacts_nested_params_without_mutating_input(self):
        params = {
            "count": 3, "label": "测试数据",
            "api_key": "SYNTHETIC_GUI_API_KEY", "password": "SYNTHETIC_GUI_PASSWORD",
            "nested": {"access_token": "SYNTHETIC_GUI_TOKEN", "keep": True},
            "items": [{"client_secret": "SYNTHETIC_GUI_SECRET"}, {"count": 2}],
        }
        original = copy.deepcopy(params)
        view = self.window.page_running
        view.start_running("data.mock", params, self.window.runtime.get_command("data.mock"))
        rendered = view.params_preview_txt.toPlainText()
        for secret in (params["api_key"], params["password"], params["nested"]["access_token"],
                       params["items"][0]["client_secret"]):
            self.assertNotIn(secret, rendered)
        summary = json.loads(rendered)
        self.assertEqual(summary["api_key"], "***")
        self.assertEqual(summary["nested"]["access_token"], "***")
        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["label"], "测试数据")
        self.assertTrue(summary["nested"]["keep"])
        self.assertEqual(summary["items"][1]["count"], 2)
        self.assertEqual(params, original)
        self.assertTrue(view.params_preview_txt.isReadOnly())
        self._assert_no_messages()

    def test_sensitive_summary_also_redacts_secret_copied_into_free_text(self):
        secret = "SYNTHETIC_GUI_COPIED_SECRET"
        params = {"token": secret, "note": f"token copied here: {secret}", "count": 7}
        self.window.page_running.start_running("example.run", params, None)
        rendered = self.window.page_running.params_preview_txt.toPlainText()
        self.assertNotIn(secret, rendered)
        self.assertEqual(json.loads(rendered)["count"], 7)
        self.assertEqual(self.window.page_running.lbl_plugin.text(), "-")
        # A later task replaces the old preview instead of retaining credentials.
        self.window.page_running.start_running("example.run", {"label": "新任务"}, None)
        self.assertEqual(json.loads(self.window.page_running.params_preview_txt.toPlainText()),
                         {"label": "新任务"})
        self._assert_no_messages()


if __name__ == "__main__":
    unittest.main()
