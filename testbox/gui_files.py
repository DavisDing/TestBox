from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets


class FileDialog(QtWidgets.QFileDialog):
    def __init__(self, parent=None, caption="", directory="", filter=""):
        super().__init__(parent, caption, directory, filter)
        self.setOption(QtWidgets.QFileDialog.Option.DontUseNativeDialog, True)
        self._configure_location_edit()
        self.directoryEntered.connect(self._sync_location)
        self._sync_location(self.directory().absolutePath())
        self.location_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+L"), self)
        self.location_shortcut.setContext(QtCore.Qt.ShortcutContext.WindowShortcut)
        self.location_shortcut.activated.connect(self._focus_location)

    def _configure_location_edit(self):
        self.location_combo = self.findChild(QtWidgets.QComboBox, "lookInCombo")
        if self.location_combo is None:
            raise RuntimeError("Qt 文件选择器缺少 Look in 目录栏")
        self.location_combo.setEditable(True)
        self.location_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.location_edit = self.location_combo.lineEdit()
        self.location_edit.setPlaceholderText("粘贴目录路径，按 Enter 跳转")
        self.location_edit.setToolTip("可粘贴目录路径（支持 ~）；按 Enter 跳转，不会提交为输入文件。")
        self.location_edit.setAccessibleName("目录路径 / Look in")
        self.location_edit.installEventFilter(self)

    def _focus_location(self):
        self.location_edit.setFocus()
        self.location_edit.selectAll()

    def _sync_location(self, directory):
        self.location_edit.setText(directory)
        self.location_edit.setProperty("invalid", False)
        self.location_edit.setToolTip("可粘贴目录路径（支持 ~）；按 Enter 跳转，不会提交为输入文件。")
        self.location_edit.style().unpolish(self.location_edit)
        self.location_edit.style().polish(self.location_edit)

    def _navigate_location(self):
        text = self.location_edit.text().strip().strip("'\"")
        try:
            directory = Path(text).expanduser()
            if not directory.is_absolute():
                directory = Path(self.directory().absolutePath()) / directory
            if text and directory.is_dir():
                self.setDirectory(str(directory))
                self._sync_location(self.directory().absolutePath())
                return
        except (OSError, RuntimeError, ValueError):
            pass
        self.location_edit.setProperty("invalid", True)
        self.location_edit.setToolTip("目录不存在或不可访问，请粘贴有效的目录路径。")
        self.location_edit.style().unpolish(self.location_edit)
        self.location_edit.style().polish(self.location_edit)
        QtWidgets.QToolTip.showText(self.location_edit.mapToGlobal(QtCore.QPoint(0, self.location_edit.height())),
                                   self.location_edit.toolTip(), self.location_edit)

    def eventFilter(self, watched, event):
        if (watched is self.location_edit and event.type() == QtCore.QEvent.Type.KeyPress
                and event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter)):
            self._navigate_location()
            return True
        return super().eventFilter(watched, event)

    @classmethod
    def _select(cls, parent, caption, directory, filter, mode, accept, options):
        dialog = cls(parent, caption, directory, filter)
        dialog.setOptions(options | QtWidgets.QFileDialog.Option.DontUseNativeDialog)
        dialog.setFileMode(mode)
        dialog.setAcceptMode(accept)
        dialog._configure_location_edit()
        dialog._sync_location(dialog.directory().absolutePath())
        try:
            if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                return dialog.selectedFiles(), dialog.selectedNameFilter()
            return [], ""
        finally:
            dialog.deleteLater()

    @classmethod
    def getOpenFileName(cls, parent=None, caption="", directory="", filter="", *, options=QtWidgets.QFileDialog.Option(0)):
        files, selected_filter = cls._select(parent, caption, directory, filter, cls.FileMode.ExistingFile, cls.AcceptMode.AcceptOpen, options)
        return (files[0] if files else ""), selected_filter

    @classmethod
    def getOpenFileNames(cls, parent=None, caption="", directory="", filter="", *, options=QtWidgets.QFileDialog.Option(0)):
        return cls._select(parent, caption, directory, filter, cls.FileMode.ExistingFiles, cls.AcceptMode.AcceptOpen, options)

    @classmethod
    def getSaveFileName(cls, parent=None, caption="", directory="", filter="", *, options=QtWidgets.QFileDialog.Option(0)):
        files, selected_filter = cls._select(parent, caption, directory, filter, cls.FileMode.AnyFile, cls.AcceptMode.AcceptSave, options)
        return (files[0] if files else ""), selected_filter

    @classmethod
    def getExistingDirectory(cls, parent=None, caption="", directory="", *, options=QtWidgets.QFileDialog.Option.ShowDirsOnly):
        files, _selected_filter = cls._select(parent, caption, directory, "", cls.FileMode.Directory, cls.AcceptMode.AcceptOpen, options)
        return files[0] if files else ""
