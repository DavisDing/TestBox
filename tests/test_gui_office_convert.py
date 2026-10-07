"""Office GUI regressions: real schemas/Runtime, no document conversion."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PySide6 import QtCore, QtTest, QtWidgets
except ModuleNotFoundError as error:
    if error.name != "PySide6":
        raise
    QT_AVAILABLE = False
else:
    QT_AVAILABLE = True
    from testbox.gui import CommandDetailFormView, DynamicSchemaForm, MainWindow

ROOT = Path(__file__).resolve().parents[1]
OFFICE_FILTER = "旧 Office 文件 (*.xls *.doc *.ppt);;所有文件 (*.*)"


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop dependency is not installed")
class GuiOfficeConvertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="testbox-gui-office-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(
            ROOT / "plugins", self.root / "plugins",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
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
        # An unconnected detail view checks the actual emitted Runtime-validated
        # parameters without starting a conversion worker or faking its result.
        self.view = CommandDetailFormView(
            self.window.runtime, runtime_root=self.root,
        )
        self.addCleanup(self.view.deleteLater)
        self.view.load_command("office.convert")
        self.view.show()
        self.app.processEvents()
        self.assertIsNotNone(self.view.form_widget, self.view.form_feedback_label.text())
        self.form = self.view.form_widget
        self.single = self.form.fields["input"][1]
        self.batch = self.form.fields["inputs"][1]
        self.submitted = QtTest.QSignalSpy(self.view.executeRequested)

    def _close_window(self):
        self.assertTrue(QtCore.QThreadPool.globalInstance().waitForDone(30_000))
        self.app.processEvents()
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()
        for message in self.messages.values():
            message.assert_not_called()

    def _file(self, name):
        path = self.root / name
        # Picker fixtures only, deliberately not genuine Office documents.
        path.write_bytes(b"GUI picker fixture; never converted")
        return str(path)

    def _click(self, button):
        QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def _submit_blocked(self, key, message):
        self.view._on_submit()
        self.assertEqual(self.submitted.count(), 0)
        self.assertFalse(self.form.error_labels[key].isHidden())
        self.assertIn(message, self.form.error_labels[key].text())
        self.assertEqual(self.window.runtime.list_tasks(), [])

    def test_official_commands_and_chinese_labels_visible_without_expanding_inputs(self):
        commands = {card[0] for card in self.window.page_catalog.all_cards}
        self.assertTrue({"office.convert", "office.inspect"} <= commands)
        self.assertEqual(commands, set(self.window.runtime.list_commands()))
        self.assertNotIn("inputs", self.form.advanced_keys)
        self.assertTrue(self.single.isVisible())
        self.assertTrue(self.batch.isVisible())
        labels = "\n".join(label.text() for label in self.form.findChildren(QtWidgets.QLabel))
        for text in ("单文件", "批量文件", "每文件超时", "批次总时限", "遇错继续", "二选一"):
            self.assertIn(text, labels)
        self.assertIsNone(self.view.preview_panel)

    def test_single_file_picker_roundtrip_and_runtime_validated_signal(self):
        path = self._file("单文件 带空格.xls")
        changed = QtTest.QSignalSpy(self.single.valueChanged)
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(path, OFFICE_FILTER)) as choose:
            self._click(self.single.btn_browse)
        self.assertEqual(choose.call_args.args[3], OFFICE_FILTER)
        self.assertEqual(changed.at(changed.count() - 1)[0], path)
        self.assertEqual(self.single.get_path(), path)
        self.assertEqual(self.form.get_values()["input"], path)
        self.assertNotIn("inputs", self.form.get_values())
        self.assertEqual(self.form.validate_locally(), (True, ""))
        self.view._on_submit()
        self.assertEqual(self.submitted.count(), 1)
        command, params = self.submitted.at(0)
        self.assertEqual(command, "office.convert")
        self.assertEqual(params, self.window.runtime.validate_params(command, self.form.get_values()))
        self.assertEqual(self.window.runtime.list_tasks(), [])

    def test_batch_picker_roundtrip_dedup_remove_and_clear(self):
        paths = [self._file("one.doc"), self._file("two.ppt")]
        changed = QtTest.QSignalSpy(self.batch.valueChanged)
        with patch.object(QtWidgets.QFileDialog, "getOpenFileNames", return_value=(paths + paths[:1], OFFICE_FILTER)) as choose:
            self._click(self.batch.btn_add)
        self.assertEqual(choose.call_args.args[3], OFFICE_FILTER)
        self.assertEqual(changed.at(changed.count() - 1)[0], paths)
        self.assertEqual(self.form.get_values()["inputs"], paths)
        self.assertNotIn("input", self.form.get_values())
        self.assertEqual(self.form.validate_locally(), (True, ""))
        self.view._on_submit()
        self.assertEqual(self.submitted.count(), 1)
        self.assertEqual(self.submitted.at(0)[1]["inputs"], paths)
        self.batch.list_widget.item(0).setSelected(True)
        self._click(self.batch.btn_remove)
        self.assertEqual(self.batch.get_paths(), paths[1:])
        self._click(self.batch.btn_clear)
        self.assertEqual(self.batch.get_paths(), [])
        self.assertNotIn("inputs", self.form.get_values())
        self.assertEqual(changed.at(changed.count() - 1)[0], [])

    def test_cancelled_pickers_preserve_selection(self):
        single = self._file("one.xls")
        batch = [self._file("two.doc")]
        self.single.set_path(single)
        self.batch.set_paths(batch)
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=("", "")):
            self._click(self.single.btn_browse)
        with patch.object(QtWidgets.QFileDialog, "getOpenFileNames", return_value=([], "")):
            self._click(self.batch.btn_add)
        self.assertEqual(self.single.get_path(), single)
        self.assertEqual(self.batch.get_paths(), batch)

    def test_missing_input_and_both_selected_block_submit_and_can_be_corrected(self):
        self._submit_blocked("input", "二选一")
        self.assertFalse(self.form.error_labels["inputs"].isHidden())
        path = self._file("one.xls")
        self.single.set_path(path)
        self.batch.set_paths([path])
        self._submit_blocked("inputs", "不能同时")
        self._click(self.batch.btn_clear)
        self.assertEqual(self.form.validate_locally(), (True, ""))
        self.assertTrue(self.form.error_labels["input"].isHidden())
        self.assertTrue(self.form.error_labels["inputs"].isHidden())
        self.batch.set_paths([path])
        self._click(self.single.btn_clear)
        self.assertEqual(self.form.validate_locally(), (True, ""))
        self.assertNotIn("input", self.form.get_values())

    def test_nonexistent_single_and_batch_paths_do_not_submit(self):
        missing = str(self.root / "missing.xls")
        self.single.set_path(missing)
        self._submit_blocked("input", "文件不存在")
        self.single.clear()
        self.batch.set_paths([self._file("good.doc"), missing])
        self._submit_blocked("inputs", "文件不存在")

    def test_edited_path_is_validated_again_before_submit(self):
        path = self._file("typed.xls")
        self.single.set_path(path)
        self.single.line_edit.selectAll()
        missing = str(self.root / "edited-missing.doc")
        QtTest.QTest.keyClicks(self.single.line_edit, missing)
        self.single.line_edit.editingFinished.emit()
        self.assertEqual(self.form.get_values()["input"], missing)
        self._submit_blocked("input", "文件不存在")
        self.single.set_path(path)
        self.assertEqual(self.form.validate_locally(), (True, ""))
        self.assertTrue(self.form.error_labels["input"].isHidden())

    def test_directory_single_and_batch_paths_do_not_submit(self):
        self.single.set_path(str(self.root))
        self._submit_blocked("input", "不是有效文件")
        self.single.clear()
        self.batch.set_paths([str(self.root)])
        self._submit_blocked("inputs", "不是有效文件")

    def test_timeout_and_continue_on_error_roundtrip_preserves_defaults_and_params(self):
        defaults = self.form.get_values()
        for key in ("timeout_seconds", "batch_timeout_seconds", "continue_on_error"):
            self.assertEqual(defaults[key], self.form.schema["properties"][key]["default"])
        params = {
            "inputs": [self._file("a.xls"), self._file("b.doc"), self._file("c.ppt")],
            "timeout_seconds": 17, "batch_timeout_seconds": 91, "continue_on_error": False,
        }
        original = copy.deepcopy(params)
        self.form.set_values(params)
        self.assertEqual(self.form.get_values(), params)
        self.view._on_submit()
        self.assertEqual(self.submitted.count(), 1)
        self.assertEqual(self.submitted.at(0)[1], params)
        self.assertEqual(params, original)
        self.form.set_values({"inputs": []})
        self.assertNotIn("inputs", self.form.get_values())
        self.view._reset_form()
        self.assertEqual(self.view.form_widget.get_values(), defaults)

    def test_schema_file_filter_overrides_command_fallback_and_array_item_hint(self):
        schema = copy.deepcopy(self.form.schema)
        schema["properties"]["input"]["x-file-filter"] = "自定义单文件 (*.legacy)"
        schema["properties"]["inputs"]["x-file-filter"] = "自定义批量 (*.old)"
        schema["properties"]["inputs"]["items"]["x-file-filter"] = "项目过滤 (*.item)"
        form = DynamicSchemaForm(schema, "office.convert")
        self.addCleanup(form.deleteLater)
        self.assertEqual(form.fields["input"][1].filter_str, "自定义单文件 (*.legacy)")
        self.assertEqual(form.fields["inputs"][1].filter_str, "自定义批量 (*.old)")
        del schema["properties"]["inputs"]["x-file-filter"]
        item_form = DynamicSchemaForm(schema, "office.convert")
        self.addCleanup(item_form.deleteLater)
        self.assertEqual(item_form.fields["inputs"][1].filter_str, "项目过滤 (*.item)")
        self.assertEqual(self.single.filter_str, OFFICE_FILTER)
        self.assertEqual(self.batch.filter_str, OFFICE_FILTER)

    def test_generic_file_arrays_are_not_images_and_evidence_filters_are_preserved(self):
        schema = {"type": "object", "properties": {
            key: {"type": "array", "items": {"type": "string", "format": "file-path"}}
            for key in ("attachments", "screenshots", "existing_reports")
        }}
        default_paths = [self._file("default.txt")]
        schema["properties"]["attachments"]["default"] = default_paths
        form = DynamicSchemaForm(schema, "example.run")
        self.addCleanup(form.deleteLater)
        self.assertEqual(form.fields["attachments"][1].filter_str, "所有文件 (*.*)")
        self.assertEqual(form.get_values()["attachments"], default_paths)
        for key in ("screenshots", "existing_reports"):
            self.assertIn("图片 / 报告文件", form.fields[key][1].filter_str)

    def test_inspect_empty_form_runs_real_host_and_displays_missing_engine_result(self):
        # The real plugin reports a successful check with available=false; this
        # never probes/runs an installed converter or mocks business success.
        missing = str(self.root / "engine-not-installed")
        with patch.dict(os.environ, {"TESTBOX_OFFICE_CONVERT_SOFFICE_PATH": missing}):
            self.window.navigate_to_command("office.inspect")
            form = self.window.page_form.form_widget
            self.assertEqual(form.fields, {})
            self.assertEqual(form.get_values(), {})
            self.assertEqual(form.validate_locally(), (True, ""))
            self.window.page_form._on_submit()
            self.assertEqual(self.window.stack.currentIndex(), 2)
            deadline = time.monotonic() + 15
            while self.window.stack.currentIndex() == 2 and time.monotonic() < deadline:
                QtTest.QTest.qWait(10)
            self.assertEqual(self.window.stack.currentIndex(), 3, "office.inspect Host completion timed out")
        result_view = self.window.page_result
        task_id = result_view.current_task_id
        record = self.window.runtime.get_task(task_id)
        result = self.window.runtime.get_task_result(task_id)
        self.assertEqual(record["command"], "office.inspect")
        self.assertEqual(record["params"], {})
        self.assertEqual(record["status"], "SUCCEEDED")
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["data"]["available"])
        self.assertEqual(result["data"]["support"], {"xls": "xlsx", "doc": "docx", "ppt": "pptx"})
        self.assertIn("DEPENDENCY_MISSING", result["warnings"])
        self.assertIn("DEPENDENCY_MISSING", result_view.warnings_txt.toPlainText())
        self.assertFalse(result_view.warnings_box.isHidden())
        self.assertEqual(result_view.current_task_info["status"], record["status"])
        self.assertIn(task_id, result_view.title_task_id_lbl.text())
        self.assertEqual(json.loads(result_view.data_summary_txt.toPlainText()), result["data"])
        self.assertTrue(self.window.runtime.get_task_report(task_id))
        self.assertEqual([task["command"] for task in self.window.runtime.list_tasks()], ["office.inspect"])

    def test_inspect_invalid_config_displays_real_host_failure(self):
        # Empty trusted engine configuration is rejected by the actual plugin.
        with patch.dict(os.environ, {"TESTBOX_OFFICE_CONVERT_SOFFICE_PATH": ""}):
            self.window.navigate_to_command("office.inspect")
            self.window.page_form._on_submit()
            deadline = time.monotonic() + 15
            while self.window.stack.currentIndex() == 2 and time.monotonic() < deadline:
                QtTest.QTest.qWait(10)
            self.assertEqual(self.window.stack.currentIndex(), 3)
        view = self.window.page_result
        task_id = view.current_task_id
        result = self.window.runtime.get_task_result(task_id)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.window.runtime.get_task(task_id)["status"], "FAILED")
        self.assertEqual(view.current_task_info["status"], "FAILED")
        self.assertIn("CONFIG_INVALID", json.dumps(result))
        self.assertIn(task_id, view.title_task_id_lbl.text())


if __name__ == "__main__":
    unittest.main()
