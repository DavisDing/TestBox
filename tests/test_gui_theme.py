from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ModuleNotFoundError as error:
    if error.name != "PySide6":
        raise
    QT_AVAILABLE = False
else:
    QT_AVAILABLE = True
    from testbox.gui import MainWindow, SettingsDialog, SingleFilePicker
    from testbox.gui_theme import THEMES, THEME_LABELS, apply_theme, read_theme

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(QT_AVAILABLE, "PySide6 desktop dependency is not installed")
class GuiThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="testbox-theme-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(ROOT / "plugins", self.root / "plugins", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.settings_path = self.root / "desktop.ini"
        self.settings = QtCore.QSettings(str(self.settings_path), QtCore.QSettings.Format.IniFormat)
        self.window = MainWindow(self.root, settings=self.settings)
        self.addCleanup(self.close_window)
        self.window.show()
        self.app.processEvents()

    def close_window(self):
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()
        apply_theme(self.app, "dark")

    def test_selection_is_immediate_and_persistent_without_resetting_form(self):
        self.window.navigate_to_command("sql.codecheck")
        form = self.window.page_form.form_widget
        form.fields["engine_version"][1].setText("3.54")
        values = form.get_values()
        dialog = SettingsDialog(self.window.runtime, self.window)
        dialog.themeChanged.connect(self.window.set_theme)
        self.assertEqual(dialog.theme_combo.count(), 3)
        for theme in THEME_LABELS:
            with self.subTest(theme=theme):
                dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData(theme))
                self.app.processEvents()
                self.assertEqual(self.app.property("testboxTheme"), theme)
                self.assertEqual(form.get_values(), values)
                reloaded = QtCore.QSettings(str(self.settings_path), QtCore.QSettings.Format.IniFormat)
                self.assertEqual(read_theme(reloaded), theme)
        dialog.close()
        dialog.deleteLater()
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.window = MainWindow(self.root, settings=QtCore.QSettings(str(self.settings_path), QtCore.QSettings.Format.IniFormat))
        self.assertEqual(self.app.property("testboxTheme"), "slate")

    def test_unknown_preference_defaults_to_dark(self):
        for value in ("unknown", 12, ["light"]):
            with self.subTest(value=value):
                self.settings.setValue("appearance/theme", value)
                self.assertEqual(read_theme(self.settings), "dark")

    def test_existing_history_status_colors_switch_without_reloading(self):
        table = self.window.page_history.table
        table.setRowCount(1)
        item = QtWidgets.QTableWidgetItem("失败")
        item.setData(QtCore.Qt.ItemDataRole.UserRole, "FAILED")
        table.setItem(0, 3, item)
        for theme, colors in THEMES.items():
            self.window.set_theme(theme)
            self.assertEqual(item.foreground().color().name(), colors["danger"].lower())

    def test_all_command_forms_and_dialogs_use_theme_palette(self):
        messages = []
        previous_handler = QtCore.qInstallMessageHandler(lambda kind, context, message: messages.append(message))
        self.addCleanup(QtCore.qInstallMessageHandler, previous_handler)
        for theme, colors in THEMES.items():
            self.window.set_theme(theme)
            for command in self.window.runtime.list_commands():
                with self.subTest(theme=theme, command=command):
                    self.window.navigate_to_command(command)
                    self.app.processEvents()
                    self.window.grab()
                    form_view = self.window.page_form
                    for editor in form_view.findChildren(QtWidgets.QLineEdit) + form_view.findChildren(QtWidgets.QPlainTextEdit):
                        if editor.isEnabled():
                            palette = editor.palette()
                            self.assertEqual(palette.color(QtGui.QPalette.ColorRole.Text).name(), colors["text"].lower())
                            self.assertEqual(palette.color(QtGui.QPalette.ColorRole.Base).name(), colors["input"].lower())
                    viewport = form_view.form_scroll.viewport()
                    self.assertEqual(viewport.palette().color(viewport.backgroundRole()).name(), colors["window"].lower())
            dialog = SettingsDialog(self.window.runtime, self.window)
            dialog.show()
            self.app.processEvents()
            dialog.grab()
            self.assertEqual(dialog.palette().color(QtGui.QPalette.ColorRole.Window).name(), colors["window"].lower())
            dialog.close()
            dialog.deleteLater()
        self.assertFalse([message for message in messages if "parse" in message.lower() and "stylesheet" in message.lower()], messages)

    def test_error_highlight_follows_theme_and_clears(self):
        picker = SingleFilePicker(parent=self.window)
        self.addCleanup(picker.deleteLater)
        for theme, colors in THEMES.items():
            self.window.set_theme(theme)
            picker.set_path(str(self.root))
            self.app.processEvents()
            self.assertTrue(picker.line_edit.property("invalid"))
            self.assertEqual(picker.line_edit.palette().color(QtGui.QPalette.ColorRole.Text).name(), colors["danger"].lower())
            picker.clear()
            self.assertFalse(picker.line_edit.property("invalid"))
            self.assertEqual(picker.line_edit.palette().color(QtGui.QPalette.ColorRole.Text).name(), colors["text"].lower())

    def test_catalog_error_empty_search_and_reload_recover(self):
        catalog = self.window.page_catalog
        self.assertEqual(catalog._catalog_state, "ready")
        self.assertTrue(catalog.state_widget.isHidden())
        catalog.search_input.setText("no-such-tool-987654")
        self.assertEqual(catalog._catalog_state, "empty_search")
        self.assertFalse(catalog.state_widget.isHidden())
        catalog.reload_tools()
        self.assertEqual(catalog._catalog_state, "empty_search")
        catalog.search_input.clear()
        self.assertEqual(catalog._catalog_state, "ready")
        with patch.object(self.window.runtime, "list_commands", side_effect=RuntimeError("fixture failure")):
            catalog.reload_tools()
            self.assertEqual(catalog._catalog_state, "error")
            self.assertTrue(catalog.refresh_btn.isEnabled())
            self.assertFalse(catalog.state_retry_btn.isHidden())
        catalog.reload_tools()
        self.assertEqual(catalog._catalog_state, "ready")
        self.assertTrue(catalog.state_widget.isHidden())
        with patch.object(self.window.runtime, "list_commands", return_value={}):
            catalog.reload_tools()
            self.assertEqual(catalog._catalog_state, "empty_plugins")
            self.assertFalse(catalog.state_widget.isHidden())
        catalog.reload_tools()
        self.assertEqual(catalog._catalog_state, "ready")
        self.assertTrue(catalog.state_widget.isHidden())

    def test_theme_text_and_primary_controls_have_readable_contrast(self):
        def luminance(value):
            color = QtGui.QColor(value)
            channels = [color.redF(), color.greenF(), color.blueF()]
            linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
            return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

        for theme, colors in THEMES.items():
            pairs = [(foreground, background) for foreground in ("text", "muted", "accent") for background in ("window", "panel", "input", "raised")]
            pairs += [("on_primary", background) for background in ("primary", "primary_hover", "primary_pressed")]
            pairs += [(token, token + "_bg") for token in ("success", "warning", "danger", "info", "abandoned")]
            for foreground, background in pairs:
                with self.subTest(theme=theme, foreground=foreground, background=background):
                    values = sorted((luminance(colors[foreground]), luminance(colors[background])))
                    self.assertGreaterEqual((values[1] + 0.05) / (values[0] + 0.05), 4.5)
