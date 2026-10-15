from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6 import QtCore, QtTest, QtWidgets
except ModuleNotFoundError as error:
    if error.name != "PySide6":
        raise
    QT_AVAILABLE = False
else:
    QT_AVAILABLE = True
    from testbox.gui import SingleFilePicker
    from testbox.gui_files import FileDialog
    from testbox.gui_theme import THEMES, apply_theme


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop dependency is not installed")
class GuiFileDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="testbox-file-dialog-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / "中文 目录"
        self.directory.mkdir()
        self.file = self.directory / "test.csv"
        self.file.write_text("id\n001\n", encoding="utf-8")
        self.dialogs = []
        self.addCleanup(self.close_dialogs)

    def close_dialogs(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()
        apply_theme(self.app, "dark")

    def dialog(self, mode=QtWidgets.QFileDialog.FileMode.ExistingFile):
        dialog = FileDialog(caption="选择测试文件", directory=str(self.root), filter="数据文件 (*.csv)")
        self.dialogs.append(dialog)
        dialog.setFileMode(mode)
        dialog.show()
        self.app.processEvents()
        return dialog

    def paste_location(self, dialog, value):
        dialog.location_edit.setFocus()
        dialog.location_edit.selectAll()
        self.app.clipboard().setText(value)
        dialog.location_edit.paste()
        QtTest.QTest.keyClick(dialog.location_edit, QtCore.Qt.Key.Key_Return)
        self.app.processEvents()

    def test_look_in_paste_jumps_without_accepting_single_or_multi_dialog(self):
        for mode in (FileDialog.FileMode.ExistingFile, FileDialog.FileMode.ExistingFiles):
            with self.subTest(mode=mode):
                dialog = self.dialog(mode)
                accepted = QtTest.QSignalSpy(dialog.accepted)
                self.assertTrue(dialog.location_combo.isEditable())
                self.paste_location(dialog, str(self.directory))
                self.assertEqual(Path(dialog.directory().absolutePath()), self.directory)
                self.assertEqual(accepted.count(), 0)
                self.assertTrue(dialog.isVisible())
                dialog.selectFile(str(self.file))
                dialog.accept()
                self.assertEqual(accepted.count(), 1)
                self.assertEqual(dialog.selectedFiles(), [str(self.file)])

    def test_bad_directory_does_not_close_or_change_location(self):
        dialog = self.dialog()
        for value in (str(self.root / "missing"), str(self.file), "", "~nonexistent_testbox_user_987654"):
            with self.subTest(value=value):
                self.paste_location(dialog, value)
                self.assertEqual(Path(dialog.directory().absolutePath()), self.root)
                self.assertTrue(dialog.isVisible())
                self.assertTrue(dialog.location_edit.property("invalid"))
                self.assertIn("目录不存在", dialog.location_edit.toolTip())
        self.paste_location(dialog, '"' + str(self.directory) + '"')
        self.assertFalse(dialog.location_edit.property("invalid"))

    def test_relative_home_and_keyboard_location_navigation(self):
        dialog = self.dialog()
        self.paste_location(dialog, self.directory.name)
        self.assertEqual(Path(dialog.directory().absolutePath()), self.directory)
        self.paste_location(dialog, "..")
        self.assertEqual(Path(dialog.directory().absolutePath()), self.root)
        self.paste_location(dialog, "~")
        self.assertEqual(Path(dialog.directory().absolutePath()), Path.home())
        dialog.location_edit.clearFocus()
        dialog._focus_location()
        self.assertTrue(dialog.location_edit.hasFocus())
        self.assertEqual(dialog.location_edit.selectedText(), str(Path.home()))

    def test_all_file_dialog_modes_share_editable_navigation_and_cancel_contract(self):
        methods = [
            ("getOpenFileName", FileDialog.FileMode.ExistingFile, FileDialog.AcceptMode.AcceptOpen, ("", "")),
            ("getOpenFileNames", FileDialog.FileMode.ExistingFiles, FileDialog.AcceptMode.AcceptOpen, ([], "")),
            ("getSaveFileName", FileDialog.FileMode.AnyFile, FileDialog.AcceptMode.AcceptSave, ("", "")),
            ("getExistingDirectory", FileDialog.FileMode.Directory, FileDialog.AcceptMode.AcceptOpen, ""),
        ]
        for method, mode, accept, cancelled in methods:
            with self.subTest(method=method):
                def check_dialog(dialog):
                    self.assertTrue(dialog.testOption(FileDialog.Option.DontUseNativeDialog))
                    self.assertTrue(dialog.location_combo.isEditable())
                    self.assertEqual(dialog.fileMode(), mode)
                    self.assertEqual(dialog.acceptMode(), accept)
                    return QtWidgets.QDialog.DialogCode.Rejected

                with patch.object(FileDialog, "exec", check_dialog):
                    result = getattr(FileDialog, method)(None, "测试", str(self.root))
                self.assertEqual(result, cancelled)

    def test_pasted_form_directory_seeds_browser_but_is_not_input_file(self):
        picker = SingleFilePicker()
        self.addCleanup(picker.deleteLater)
        picker.set_path(str(self.directory))
        self.assertEqual(picker.get_path(), "")
        with patch.object(FileDialog, "getOpenFileName", return_value=(str(self.file), "")) as choose:
            picker._choose_file()
        self.assertEqual(choose.call_args.args[2], str(self.directory))
        self.assertEqual(picker.get_path(), str(self.file))

    def test_location_remains_readable_across_themes(self):
        from PySide6 import QtGui

        dialog = self.dialog()
        for theme, colors in THEMES.items():
            with self.subTest(theme=theme):
                apply_theme(self.app, theme)
                self.app.processEvents()
                palette = dialog.location_edit.palette()
                self.assertEqual(palette.color(QtGui.QPalette.ColorRole.Base).name(), colors["input"].lower())
                self.assertEqual(palette.color(QtGui.QPalette.ColorRole.Text).name(), colors["text"].lower())


if __name__ == "__main__":
    unittest.main()
