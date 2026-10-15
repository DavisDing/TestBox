"""Headless UI regressions for the manifest-driven local plugin catalog."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
try:
    from PySide6 import QtCore, QtTest, QtWidgets
except ModuleNotFoundError as error:
    if error.name != 'PySide6':
        raise
    QT_AVAILABLE = False
else:
    QT_AVAILABLE = True
    from testbox.gui import MainWindow, ToolCatalogView
    from testbox.gui_theme import THEMES

from test_catalog import make_catalog_plugin


@unittest.skipUnless(QT_AVAILABLE, 'PySide6 desktop dependency is not installed')
class GuiCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='testbox-catalog-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plugin = make_catalog_plugin(self.root / 'plugins')
        make_catalog_plugin(self.root / 'plugins', name='other-plugin', commands=('other.run',),
                            category='another-group', group_label='另一个分组')
        self.settings = QtCore.QSettings(str(self.root / 'desktop.ini'), QtCore.QSettings.Format.IniFormat)
        self.window = MainWindow(self.root, settings=self.settings)
        self.addCleanup(self.close_window)
        self.window.show()
        self.app.processEvents()
        self.catalog = self.window.page_catalog

    def close_window(self):
        self.window.close()
        self.window.runtime.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def visible_commands(self):
        return [row[0] for row in self.catalog.all_cards if not row[-1].isHidden()]

    def card(self, command='sample.run'):
        return next(row[-1] for row in self.catalog.all_cards if row[0] == command)

    def test_new_plugin_defines_group_labels_search_and_declaration_order(self):
        self.assertEqual(len(self.catalog.all_cards), 3)
        self.assertEqual(self.catalog.category_labels['custom-group'], '自定义工具')
        headings = [item.text() for item in self.catalog._decorations if isinstance(item, QtWidgets.QLabel)]
        self.assertIn('自定义工具', headings)
        self.assertEqual(sum('sample-plugin' in text for text in headings), 1)
        self.catalog.search_input.setText(' 自定义工具 ')
        self.assertEqual(self.visible_commands(), ['sample.run', 'sample.inspect'])
        self.catalog.category_combo.setCurrentIndex(self.catalog.category_combo.findData('another-group'))
        self.assertEqual(self.visible_commands(), [])
        self.assertEqual(self.catalog._catalog_state, 'empty_search')
        self.catalog.search_input.clear()
        self.assertEqual(self.visible_commands(), ['other.run'])

    def test_favorite_click_does_not_navigate_execute_or_record_recent(self):
        card = self.card()
        with patch.object(self.window.runtime, 'run', side_effect=AssertionError('must not run')):
            QtTest.QTest.mouseClick(card.favorite_btn, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self.assertEqual(self.catalog.preferences.recent, [])
        self.assertEqual(self.window.runtime.list_tasks(), [])
        self.catalog.mode_tabs.setCurrentIndex(1)
        self.assertEqual(self.visible_commands(), ['sample.run'])
        self.catalog.search_input.setText('操作0')
        self.assertEqual(self.visible_commands(), ['sample.run'])
        self.catalog.category_combo.setCurrentIndex(self.catalog.category_combo.findData('another-group'))
        self.assertEqual(self.visible_commands(), [])
        self.catalog.category_combo.setCurrentIndex(0)
        QtTest.QTest.mouseClick(card.favorite_btn, QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(self.visible_commands(), [])

    def test_successful_configuration_only_updates_recent_and_keyboard_works(self):
        self.card().setFocus()
        QtTest.QTest.keyClick(self.card(), QtCore.Qt.Key.Key_Return)
        self.assertEqual(self.window.stack.currentIndex(), 1)
        self.assertEqual(self.catalog.preferences.recent, [('sample-plugin', 'sample.run')])
        self.window.navigate_to_command('other.run')
        self.window.navigate_to_command('sample.run')
        before = list(self.catalog.preferences.recent)
        with patch.object(self.window.runtime, 'get_command_schema', side_effect=ValueError('broken schema')):
            self.window.navigate_to_command('sample.inspect')
        self.assertFalse(self.window.page_form.submit_btn.isEnabled())
        self.assertEqual(self.catalog.preferences.recent, before)
        with patch('testbox.gui.DynamicSchemaForm', side_effect=ValueError('broken form')):
            self.window.navigate_to_command('sample.inspect')
        self.assertEqual(self.catalog.preferences.recent, before)
        self.assertEqual(self.window.runtime.list_tasks(), [])
        self.window.switch_page(0)
        self.catalog.mode_tabs.setCurrentIndex(2)
        layout_commands = [self.catalog.cards_layout.itemAt(index).widget().command_name
                           for index in range(self.catalog.cards_layout.count())
                           if hasattr(self.catalog.cards_layout.itemAt(index).widget(), 'command_name')]
        self.assertEqual(layout_commands, ['sample.run', 'other.run'])

    def test_refresh_preserves_filters_but_explicitly_rescans(self):
        self.catalog.search_input.setText('操作')
        self.catalog.category_combo.setCurrentIndex(self.catalog.category_combo.findData('custom-group'))
        with patch.object(self.window.runtime, 'reload_plugins', wraps=self.window.runtime.reload_plugins) as reload:
            self.window.switch_page(0)
            reload.assert_not_called()
            QtTest.QTest.mouseClick(self.catalog.refresh_btn, QtCore.Qt.MouseButton.LeftButton)
            reload.assert_called_once()
        self.assertEqual(self.catalog.search_input.text(), '操作')
        self.assertEqual(self.catalog.category_combo.currentData(), 'custom-group')
        make_catalog_plugin(self.root / 'plugins', name='new-plugin', commands=('new.run',),
                            category='new-group', group_label='新增分组')
        self.window.page_diagnostics._rescan_plugins()
        self.assertIn('new.run', [row[0] for row in self.catalog.all_cards])
        self.assertIn('new-group', self.catalog.category_labels)
        self.assertEqual(self.window.page_diagnostics.plugins_table.rowCount(), 3)

    def test_unavailable_favorite_retained_removable_and_links_to_diagnostics(self):
        key = ('sample-plugin', 'sample.run')
        self.catalog._toggle_favorite(key)
        self.window.navigate_to_command('sample.run')
        self.window.runtime.uninstall_plugin('sample-plugin')
        self.window.switch_page(0)
        self.catalog.mode_tabs.setCurrentIndex(1)
        self.assertEqual(self.visible_commands(), [])
        self.assertEqual(self.catalog.preferences.favorites, [key])
        row = next(widget for widget in self.catalog._decorations
                   if widget.findChildren(QtWidgets.QPushButton))
        buttons = row.findChildren(QtWidgets.QPushButton)
        QtTest.QTest.mouseClick(next(button for button in buttons if '诊断' in button.text()), QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.stack.currentIndex(), 5)
        self.window.switch_page(0)
        self.catalog.mode_tabs.setCurrentIndex(2)
        self.assertEqual(self.visible_commands(), [])
        self.assertEqual(self.catalog.preferences.recent, [key])
        self.catalog.mode_tabs.setCurrentIndex(1)
        row = next(widget for widget in self.catalog._decorations if widget.findChildren(QtWidgets.QPushButton))
        QtTest.QTest.mouseClick(next(button for button in row.findChildren(QtWidgets.QPushButton)
                                   if button.text() == '取消收藏'), QtCore.Qt.MouseButton.LeftButton)
        self.assertEqual(self.catalog.preferences.favorites, [])
        self.assertEqual(self.catalog._catalog_state, 'empty_favorites')

    def test_install_upgrade_and_uninstall_sync_catalog_after_confirmation(self):
        source = make_catalog_plugin(self.root / 'source', name='installed-plugin', commands=('installed.run',),
                                     category='installed-group', group_label='安装的分组')
        archive = self.window.runtime.package_plugin(source, self.root / 'plugin.zip')
        diagnostics = self.window.page_diagnostics
        with patch('testbox.gui.FileDialog.getOpenFileName', return_value=(str(archive), '')), \
             patch.object(QtWidgets.QMessageBox, 'question', return_value=QtWidgets.QMessageBox.StandardButton.Yes), \
             patch.object(QtWidgets.QMessageBox, 'information'):
            diagnostics._on_import_plugin()
            self.assertIn('installed.run', [row[0] for row in self.catalog.all_cards])
            self.catalog._toggle_favorite(('installed-plugin', 'installed.run'))
            make_catalog_plugin(self.root / 'source', name='installed-plugin', commands=('installed.run',),
                                category='installed-group', group_label='安装的分组', version='2.0.0')
            self.window.runtime.package_plugin(source, archive)
            diagnostics._on_import_plugin()
            self.assertEqual(self.card('installed.run').entry['plugin_version'], '2.0.0')
            self.assertIn(('installed-plugin', 'installed.run'), self.catalog.preferences.favorites)
            for row in range(diagnostics.plugins_table.rowCount()):
                if diagnostics.plugins_table.item(row, 0).data(QtCore.Qt.ItemDataRole.UserRole)['name'] == 'installed-plugin':
                    diagnostics.plugins_table.selectRow(row)
                    break
            diagnostics._on_uninstall_plugin()
        self.assertNotIn('installed.run', [row[0] for row in self.catalog.all_cards])
        self.assertIn(('installed-plugin', 'installed.run'), self.catalog.preferences.favorites)

    def test_settings_restore_theme_changes_and_dynamic_labels_are_plain_text(self):
        path = self.plugin / 'manifest.yaml'
        path.write_text(path.read_text(encoding='utf-8').replace('新工具', "'<b>新工具</b>'")
                        .replace('自定义工具', "'<b>自定义分组</b>'"), encoding='utf-8')
        self.catalog._rescan_tools()
        self.catalog._toggle_favorite(('sample-plugin', 'sample.run'))
        self.window.navigate_to_command('sample.run')
        for theme in THEMES:
            self.window.set_theme(theme)
        self.assertEqual(self.catalog.preferences.favorites, [('sample-plugin', 'sample.run')])
        labels = self.card().findChildren(QtWidgets.QLabel)
        self.assertTrue(any('<b>新工具</b>' in label.text() for label in labels))
        self.assertTrue(all(label.textFormat() == QtCore.Qt.TextFormat.PlainText for label in labels))
        restored_settings = QtCore.QSettings(self.settings.fileName(), QtCore.QSettings.Format.IniFormat)
        restored = ToolCatalogView(self.window.runtime, settings=restored_settings)
        self.addCleanup(restored.deleteLater)
        self.assertEqual(restored.preferences.favorites, self.catalog.preferences.favorites)
        self.assertEqual(restored.preferences.recent, self.catalog.preferences.recent)
        saved = json.loads(restored_settings.value('catalog/favorites'))
        self.assertEqual(saved, [{'plugin_name': 'sample-plugin', 'command_name': 'sample.run'}])

    def test_conflicting_group_labels_keep_one_group_and_visible_notice(self):
        make_catalog_plugin(self.root / 'plugins', name='a-plugin', commands=('a.run',),
                            group_label='共享分组')
        self.catalog._rescan_tools()
        self.assertEqual(self.catalog.category_labels['custom-group'], '共享分组')
        self.assertIn('不同显示名', self.catalog.preference_notice.text())
        self.assertEqual(len(self.catalog.all_cards), 4)


if __name__ == '__main__':
    unittest.main()
