"""Real optional-Qt regressions; all Runtime writes stay in temporary roots."""
from __future__ import annotations

import copy
from contextlib import closing
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
    from PySide6 import QtGui
    from testbox.gui import DataMockForm, DynamicSchemaForm, MainWindow
    from testbox.gui_theme import THEMES, apply_theme, build_stylesheet


from testbox.core.schema_validator import SchemaValidationError, SchemaValidator

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {"data.mock", "sql.parse", "sql.select", "sql.codecheck", "evidence.build", "data.preview", "data.compare", "data.check", "sql.diff", "sql.preview"}


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
        self.theme_settings = QtCore.QSettings(str(self.root / "desktop.ini"), QtCore.QSettings.Format.IniFormat)
        self.window = MainWindow(self.root, settings=self.theme_settings)
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

    def test_imported_fields_preserve_unedited_metadata_and_generated_ddl(self):
        import sqlite3
        self.window.navigate_to_command("data.mock")
        form = self.window.page_form.form_widget
        fields = [{"name": "id", "type": "INTEGER", "generator": "integer_random",
                   "options": {}, "nullable": False, "default": "99",
                   "foreign_table": "parent", "foreign_field": "id"},
                  {"name": "disabled", "type": "INTEGER", "generator": "constant",
                   "options": {"value": 3}, "enabled": False}]
        original = copy.deepcopy(fields)
        form.mode_combo.setCurrentIndex(2)
        form._set_imported_tables([{"name": "sample", "fields": fields}])
        params = form.get_values()
        for key in ("nullable", "default", "foreign_table", "foreign_field"):
            self.assertEqual(params["fields"][0][key], original[0][key])
        self.assertFalse(params["fields"][1]["enabled"])
        params["fields"][1]["options"]["value"] = 9
        self.assertEqual(fields, original)
        params.update(count=1, format="sql", sql_dialect="sqlite", sql_create_table=True)
        task, result = self.window.runtime.run("data.mock", params)
        self.assertEqual(result.status, "success", result.message)
        script = self.window.runtime.get_task_output_path(task, result.files[0]).read_text(encoding="utf-8")
        with closing(sqlite3.connect(":memory:")) as database:
            database.executescript(script)
            columns = database.execute("PRAGMA table_info(sample)").fetchall()
        self.assertEqual(len(columns), 1)
        self.assertEqual(columns[0][1:5], ("id", "INTEGER", 1, "'99'"))

    def test_field_metadata_merge_allows_clearing_editable_values(self):
        from testbox.gui import DataMockFieldRow
        source = {"name": "id", "generator": "constant", "options": {"value": "old"},
                  "comment": "old", "nullable": False, "default": "99"}
        row = DataMockFieldRow(source)
        self.addCleanup(row.deleteLater)
        row.comment_edit.clear()
        row.options_edit.clear()
        row.generator_combo.setCurrentIndex(0)
        value = row.get_value()
        self.assertNotIn("comment", value)
        self.assertNotIn("generator", value)
        self.assertEqual(value["options"], {})
        self.assertFalse(value["nullable"])
        self.assertEqual(source["comment"], "old")

    def test_default_root_import_preview_uses_installed_bundle_and_user_storage(self):
        from testbox.core.runtime import Runtime
        package = self.root / "site-packages" / "testbox"
        (package / "core").mkdir(parents=True)
        shutil.copytree(self.root / "plugins", package / "_bundled_plugins")
        source = self.root / "schema.sql"
        source.write_text("CREATE TABLE sample (id INTEGER NOT NULL DEFAULT 99);", encoding="utf-8")
        data = self.root / "user-data"
        with patch("testbox.core.runtime.__file__", str(package / "core" / "runtime.py")), \
                patch.object(Runtime, "_user_data_dir", return_value=data):
            runtime = Runtime()
            try:
                form = DataMockForm(runtime.get_command_schema("data.mock"), runtime=runtime)
                self.addCleanup(form.deleteLater)
                form.source_picker.set_path(str(source))
                with patch.object(QtCore.QThreadPool, "start") as start:
                    form._preview_source()
                worker = start.call_args.args[0]
                self.assertIsNone(worker.root)
                worker.run()
                self.assertEqual(form.imported_tables[0]["name"], "sample")
                self.assertEqual(len(runtime.list_tasks()), 1)
                self.assertTrue((data / "workspace").is_dir())
                self.assertFalse((package / "workspace").exists())
                self.assertFalse((package / "plugins").exists())
                self._assert_no_messages()
            finally:
                runtime.close()

    def test_generic_array_history_roundtrip_preserves_objects_and_commas(self):
        schema = self.window.runtime.get_command_schema("data.check")
        form = DynamicSchemaForm(schema, "data.check")
        self.addCleanup(form.deleteLater)
        rules = [{"type": "required", "fields": ["id", "a,b"]}]
        form.set_values({"rules": rules})
        self.assertEqual(form.get_values()["rules"], rules)
        generic = DynamicSchemaForm({"type": "object", "properties": {
            "items": {"type": "array"},
        }})
        self.addCleanup(generic.deleteLater)
        for value in ([], ["a,b", "中文"], [{"nested": ["x,y", {"value": 1}]}]):
            generic.set_values({"items": value})
            self.assertEqual(generic.get_values()["items"], value)

    def test_legacy_rules_history_reruns_as_fields_without_losing_values(self):
        params = {"count": 2, "format": "json", "seed": 7, "rules": [{
            "name": "only", "generator": "constant", "options": {"value": "EXPECTED"},
        }]}
        first, result = self.window.runtime.run("data.mock", params)
        self.assertEqual(result.status, "success", result.message)
        history = self.window.runtime.get_task(first)["params"]
        self.window.navigate_to_command("data.mock", history)
        restored = self.window.page_form.form_widget.get_values()
        self.assertNotIn("rules", restored)
        second, rerun = self.window.runtime.run("data.mock", restored)
        self.assertEqual(rerun.status, "success", rerun.message)
        self.assertEqual(self.window.runtime.get_task_output_path(first, result.files[0]).read_bytes(),
                         self.window.runtime.get_task_output_path(second, rerun.files[0]).read_bytes())

    def test_evidence_optional_rows_do_not_hide_the_case_card(self):
        spec = importlib.util.spec_from_file_location(
            "audit_interactive", ROOT / "plugins" / "evidence-tool" / "src" / "interactive.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        items = [SimpleNamespace(case_name="One", checkpoint="check", step_name="",
                                 step_description="", expected_result=""),
                 SimpleNamespace(case_name="Two", checkpoint="other", step_name="step",
                                 step_description="description", expected_result="expected")]

        def inspect(session):
            try:
                for index in (0, 1, 0):
                    session.index = index
                    session.refresh()
                    for key in ("case", "checkpoint"):
                        label, value = session.field_rows[key]
                        self.assertFalse(label.isHidden())
                        self.assertFalse(value.isHidden())
                        self.assertFalse(value.parentWidget().isHidden())
                    for key in ("step", "desc", "expected"):
                        label, value = session.field_rows[key]
                        self.assertEqual(label.isHidden(), index == 0)
                        self.assertEqual(value.isHidden(), index == 0)
                return QtWidgets.QDialog.DialogCode.Rejected
            finally:
                session.reject()
                session.deleteLater()

        with patch.object(QtWidgets.QDialog, "exec", inspect):
            module.run_session(items, self.root / "captures", Mock())

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
        self.assertEqual(diagnostics.plugins_table.rowCount(), 9)
        self.assertEqual(diagnostics.unavailable_table.rowCount(), 0)
        self.assertEqual(diagnostics.available_stack.currentIndex(), 1)
        self.assertEqual(
            diagnostics.runtime_diagnostic_labels["workspace_dir"].text(),
            str(self.root / "workspace"),
        )
        self.window.nav_list.setCurrentRow(0)
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self._assert_no_messages()

    def test_catalog_leaves_loading_state_after_commands_are_loaded(self):
        catalog = self.window.page_catalog
        self.assertEqual(catalog._catalog_state, "ready")
        self.assertFalse(catalog.state_widget.isVisible())
        self.assertGreater(len(catalog.all_cards), 0)

    def test_theme_styles_keep_form_text_visible_and_switch_palette(self):
        application = self.app
        for theme, colors in THEMES.items():
            with self.subTest(theme=theme):
                apply_theme(application, theme)
                stylesheet = build_stylesheet(theme)
                self.assertIn("QWidget", stylesheet)
                self.assertIn(colors["text"], stylesheet)
                self.assertIn(colors["panel"], stylesheet)
                self.assertEqual(application.property("testboxTheme"), theme)
                self.assertEqual(application.palette().color(QtGui.QPalette.ColorRole.Window).name().upper(), colors["window"].upper())
        apply_theme(application, "dark")

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

    def test_codecheck_dropdown_modes_hide_and_omit_inactive_options(self):
        schema = self.window.runtime.get_command_schema("sql.codecheck")
        form = DynamicSchemaForm(schema, "sql.codecheck")
        self.addCleanup(form.deleteLater)
        form.set_values({"mapping_mode": "database", "output_mode": "select_only",
                         "mapping_input": "/unused/mapping.xlsx", "mapping_source": "same_workbook",
                         "file_layout": "combined_rows", "result_table": "audit_results",
                         "result_scope": "all", "store_scope": "all", "create_policy": "create_new",
                         "statement_layout": "separate", "union_batch_size": 10,
                         "code_table": {"table_parts": ["mapping"]}})
        values = form.get_values()
        self.assertEqual(values["output_mode"], "select_only")
        self.assertEqual(form.fields["output_mode"][1].currentText(), "仅生成SELECT")
        self.assertEqual(form.fields["statement_layout"][1].currentText(), "各用例独立SQL")
        for key in ("mapping_input", "mapping_source", "file_layout", "store_scope", "result_table", "create_policy", "union_batch_size"):
            self.assertNotIn(key, values)
            self.assertTrue(form.field_containers[key].isHidden())
        form.set_values({"mapping_mode": "file", "mapping_source": "same_workbook", "file_layout": "combined_rows",
                         "output_mode": "insert_only", "statement_layout": "union_all"})
        values = form.get_values()
        self.assertEqual(values["output_mode"], "insert_only")
        self.assertEqual(values["union_batch_size"], 10)
        for key in ("mapping_input", "code_table", "result_scope", "mapping_options"):
            self.assertNotIn(key, values)
        self.assertIn("result_table", values)
        form.set_values({"output_mode": "select_and_insert", "mapping_source": "separate_file"})
        values = form.get_values()
        self.assertIn("mapping_input", values)
        self.assertIn("result_scope", values)
        self.assertNotIn("file_layout", values)
        self.assertIn("xlsx", form._file_filter("test_plan_input", schema["properties"]["test_plan_input"]))

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
