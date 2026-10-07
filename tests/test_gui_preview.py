from __future__ import annotations
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from PySide6 import QtCore, QtTest, QtWidgets
from testbox.gui import MainWindow

ROOT = Path(__file__).resolve().parents[1]


class GuiPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "plugins",self.root / "plugins",ignore=shutil.ignore_patterns("__pycache__"))
        self.window = MainWindow(self.root)
        self.addCleanup(self.close)
        self.window.show()
        self.app.processEvents()

    def close(self):
        self.assertTrue(QtCore.QThreadPool.globalInstance().waitForDone(30_000))
        self.app.processEvents()
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def await_preview(self,panel):
        end = time.monotonic() + 15
        while panel.worker is not None and time.monotonic() < end:
            QtTest.QTest.qWait(10)
        self.assertIsNone(panel.worker,"preview timed out")
        self.assertIn("任务",panel.status.text())

    def test_adjust_preview_invalidate_then_reparse_real_host(self):
        p = self.root / "a.txt"
        p.write_text("id||value<EOR>001||A<EOR>002||B<EOR>",encoding="utf-8")
        self.window.navigate_to_command("data.preview", {"input":str(p),"options":{"format":"txt","delimiter":"||","record_separator":"<EOR>"}})
        panel = self.window.page_form.preview_panel
        source,editors,button = panel.editors[0]
        QtTest.QTest.mouseClick(button,QtCore.Qt.MouseButton.LeftButton)
        self.await_preview(panel)
        self.assertEqual(panel.table.rowCount(),2)
        self.assertEqual(panel.table.item(0,0).text(),"001")
        first = panel.last_task_id
        editors["delimiter"].setText("|")
        self.assertEqual(panel.table.rowCount(),0)
        self.assertIn("失效",panel.status.text())
        editors["delimiter"].setText("||")
        panel.preview(source,editors)
        self.await_preview(panel)
        self.assertNotEqual(panel.last_task_id,first)
        self.assertEqual(panel.table.item(1,1).text(),"B")
        self.assertEqual(self.window.page_form.form_widget.get_values()["options"]["delimiter"],"||")
        self.assertEqual(p.read_text(),"id||value<EOR>001||A<EOR>002||B<EOR>")

    def test_advanced_options_editor_and_preview_stay_synchronized(self):
        p=self.root / "a.csv"
        p.write_text("id;value\n001;A\n",encoding="utf-8")
        self.window.navigate_to_command("data.preview",{"input":str(p)})
        panel=self.window.page_form.preview_panel
        _kind,widget = self.window.page_form.form_widget.fields["options"]
        widget.setText(json.dumps({"delimiter":";","max_rows":7}))
        source,editors,button=panel.editors[0]
        self.assertEqual(editors["delimiter"].text(),";")
        panel.preview(source,editors)
        self.await_preview(panel)
        self.assertEqual(panel.table.columnCount(),2)
        self.assertEqual(self.window.page_form.form_widget.get_values()["options"]["max_rows"],7)

    def test_preview_parse_failure_does_not_show_old_success(self):
        p=self.root / "a.csv"
        p.write_text("id,id\n1,2\n",encoding="utf-8")
        self.window.navigate_to_command("data.preview",{"input":str(p)})
        panel=self.window.page_form.preview_panel
        source,editors,_button=panel.editors[0]
        panel.preview(source,editors)
        self.await_preview(panel)
        self.assertIn("失败",panel.status.text())
        self.assertEqual(panel.table.rowCount(),0)
        self.assertEqual(self.window.runtime.get_task(panel.last_task_id)["status"],"FAILED")

    def test_input_change_during_preview_discards_outdated_response(self):
        p=self.root / "a.csv"
        p.write_text("id\n001\n",encoding="utf-8")
        self.window.navigate_to_command("data.preview",{"input":str(p)})
        panel=self.window.page_form.preview_panel
        source,editors,_button=panel.editors[0]
        panel.preview(source,editors)
        editors["delimiter"].setText(";")
        self.await_preview(panel)
        self.assertIn("失效",panel.status.text())
        self.assertEqual(panel.table.rowCount(),0)

    def test_sql_text_editor_round_trip_preserves_newlines(self):
        self.assertIn("sql.diff", self.window.runtime.list_commands())
        text="CREATE TABLE t (\n id INTEGER,\n name VARCHAR(10)\n);"
        self.window.navigate_to_command("sql.diff",{"left_text":text,"right_text":text})
        form=self.window.page_form.form_widget
        self.assertEqual(form.get_values()["left_text"],text)
        self.assertEqual(form.fields["left_text"][0],"multiline")
        panel=self.window.page_form.preview_panel
        source,editors,_button=panel.editors[0]
        panel.preview(source,editors)
        self.await_preview(panel)
        self.assertGreater(panel.table.rowCount(),0)
        self.assertNotIn("失败",panel.status.text())
        form.fields["left_mode"][1].setCurrentText("structure")
        self.assertIn("失效",panel.status.text())
        self.assertEqual(panel.table.rowCount(),0)

    def test_compare_preview_and_submit_use_same_separator_through_host(self):
        left=self.root / "expected.txt"
        right=self.root / "actual.json"
        left.write_text("id||value<EOR>001||A<EOR>",encoding="utf-8")
        right.write_text('[{"id":"001","value":"A"}]',encoding="utf-8")
        self.window.navigate_to_command("data.compare",{"left":str(left),"right":str(right)})
        view=self.window.page_form
        panel=view.preview_panel
        source,editors,_button=panel.editors[0]
        editors["format"].setCurrentText("txt")
        editors["delimiter"].setText("||")
        editors["record_separator"].setText("<EOR>")
        panel.preview(source,editors)
        self.await_preview(panel)
        self.assertEqual(panel.table.item(0,0).text(),"001")
        view._on_submit()
        end=time.monotonic()+15
        while self.window.stack.currentIndex()==2 and time.monotonic()<end:
            QtTest.QTest.qWait(10)
        self.assertEqual(self.window.stack.currentIndex(),3)
        task=self.window.page_result.current_task_id
        result=self.window.runtime.get_task_result(task)
        self.assertEqual(result["status"],"success")
        self.assertTrue(result["data"]["equal"])
        self.assertEqual(self.window.runtime.get_task(task)["params"]["left_options"]["delimiter"],"||")


if __name__ == "__main__":
    unittest.main()
