from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from testbox.core.runtime import Runtime


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ModuleNotFoundError as error:
        raise SystemExit("TestBox GUI 需要桌面依赖，请执行: pip install -e '.[desktop]'") from error
    return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt()


class AnnotationCanvas(QtWidgets.QGraphicsView):
    def __init__(self, pixmap):
        super().__init__(); self.scene = QtWidgets.QGraphicsScene(self); self.setScene(self.scene); self.scene.addPixmap(pixmap)
        self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height()); self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag); self.mode = "pen"; self.start = None; self.active = None; self.history = []; self.crop_rect = None

    def undo(self):
        if self.history:
            item = self.history.pop(); self.scene.removeItem(item)

    def set_mode(self, mode):
        self.mode = mode; self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag if mode == "pan" else QtWidgets.QGraphicsView.DragMode.NoDrag)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton or self.mode == "pan": return super().mousePressEvent(event)
        point = self.mapToScene(event.position().toPoint()); self.start = point
        if self.mode == "text":
            value, accepted = QtWidgets.QInputDialog.getText(self, "添加文字", "文字内容")
            if accepted and value:
                item = self.scene.addText(value, QtGui.QFont("", 18)); item.setDefaultTextColor(QtGui.QColor("#ef4444")); item.setPos(point); self.history.append(item)
            self.start = None
        elif self.mode in {"pen", "highlighter"}:
            path = QtGui.QPainterPath(point); item = QtWidgets.QGraphicsPathItem(path)
            color, width = (QtGui.QColor(255, 225, 50, 105), 22) if self.mode == "highlighter" else (QtGui.QColor("#ef4444"), 5)
            item.setPen(QtGui.QPen(color, width, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap)); self.scene.addItem(item); self.active = item

    def mouseMoveEvent(self, event):
        if self.start is None: return super().mouseMoveEvent(event)
        current = self.mapToScene(event.position().toPoint())
        if self.mode in {"pen", "highlighter"}:
            path = self.active.path(); path.lineTo(current); self.active.setPath(path)
        elif self.mode in {"rect", "ellipse", "crop"}:
            if self.active is not None: self.scene.removeItem(self.active)
            rectangle = QtCore.QRectF(self.start, current).normalized(); pen = QtGui.QPen(QtGui.QColor("#ef4444"), 5)
            self.active = self.scene.addEllipse(rectangle, pen) if self.mode == "ellipse" else self.scene.addRect(rectangle, pen)

    def mouseReleaseEvent(self, event):
        if self.start is not None and self.active is not None:
            if self.mode == "crop": self.crop_rect = self.active.rect().intersected(self.scene.sceneRect()); self.scene.removeItem(self.active)
            else: self.history.append(self.active)
        self.start = self.active = None

    def zoom(self, factor): self.scale(factor, factor)

    def result_pixmap(self):
        source = self.crop_rect if self.crop_rect and self.crop_rect.width() > 2 and self.crop_rect.height() > 2 else self.scene.sceneRect()
        image = QtGui.QImage(int(source.width()), int(source.height()), QtGui.QImage.Format.Format_ARGB32); image.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(image); self.scene.render(painter, QtCore.QRectF(0, 0, image.width(), image.height()), source); painter.end()
        return QtGui.QPixmap.fromImage(image)


class AnnotationDialog(QtWidgets.QDialog):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent); self.setWindowTitle("标注截图"); self.resize(1100, 760)
        self.canvas = AnnotationCanvas(pixmap)
        toolbar = QtWidgets.QHBoxLayout(); group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for label, mode in (("移动", "pan"), ("画笔", "pen"), ("矩形", "rect"), ("圆形", "ellipse"), ("荧光笔", "highlighter"), ("文字", "text"), ("裁剪", "crop")):
            button = QtWidgets.QToolButton(); button.setText(label); button.setCheckable(True); button.setChecked(mode == "pen"); group.addButton(button); toolbar.addWidget(button); button.clicked.connect(lambda checked=False, value=mode: self.canvas.set_mode(value))
        zoom_out = QtWidgets.QToolButton(); zoom_out.setText("缩小"); zoom_out.clicked.connect(lambda: self.canvas.zoom(0.8)); toolbar.addWidget(zoom_out)
        zoom_in = QtWidgets.QToolButton(); zoom_in.setText("放大"); zoom_in.clicked.connect(lambda: self.canvas.zoom(1.25)); toolbar.addWidget(zoom_in)
        undo = QtWidgets.QPushButton("撤销"); undo.clicked.connect(self.canvas.undo); toolbar.addWidget(undo); toolbar.addStretch()
        cancel = QtWidgets.QPushButton("取消"); cancel.clicked.connect(self.reject); save = QtWidgets.QPushButton("保存"); save.setDefault(True); save.clicked.connect(self.accept); toolbar.addWidget(cancel); toolbar.addWidget(save)
        layout = QtWidgets.QVBoxLayout(self); layout.addLayout(toolbar); layout.addWidget(self.canvas, 1)
        QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Undo, self, activated=self.canvas.undo)

    def result_pixmap(self): return self.canvas.result_pixmap()


class SchemaForm(QtWidgets.QWidget):
    run_requested = QtCore.Signal(str, dict)

    def __init__(self, command: str, manifest, parent=None):
        super().__init__(parent); self.command = command; self.manifest = manifest; self.fields = {}
        spec = next(item for item in manifest.commands if item.name == command); schema = json.loads((manifest.path / spec.input_schema).read_text(encoding="utf-8")) if spec.input_schema else {"properties": {}}
        root = QtWidgets.QVBoxLayout(self); title = QtWidgets.QLabel(command); title.setObjectName("pageTitle"); root.addWidget(title)
        subtitle = QtWidgets.QLabel(f"{spec.description}  ·  {manifest.name} {manifest.version}"); subtitle.setObjectName("muted"); root.addWidget(subtitle)
        form = QtWidgets.QFormLayout(); form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for key, definition in schema.get("properties", {}).items():
            widget = self._widget(key, definition); self.fields[key] = (widget, definition); form.addRow(definition.get("description", key), widget)
        root.addLayout(form); root.addStretch(); run = QtWidgets.QPushButton("运行任务"); run.setObjectName("primary"); run.clicked.connect(self._submit); root.addWidget(run)

    def _widget(self, key, definition):
        if definition.get("enum"):
            widget = QtWidgets.QComboBox(); widget.addItems(map(str, definition["enum"])); return widget
        if definition.get("type") == "boolean":
            widget = QtWidgets.QCheckBox(); widget.setChecked(definition.get("default", False)); return widget
        widget = QtWidgets.QLineEdit(); widget.setPlaceholderText("JSON 数组" if definition.get("type") == "array" else "")
        if definition.get("format") == "file-path":
            container = QtWidgets.QWidget(); layout = QtWidgets.QHBoxLayout(container); layout.setContentsMargins(0, 0, 0, 0); layout.addWidget(widget)
            browse = QtWidgets.QToolButton(); browse.setText("选择…"); browse.clicked.connect(lambda: self._browse(widget)); layout.addWidget(browse); container.editor = widget; return container
        return widget

    def _browse(self, editor):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择输入文件")
        if path: editor.setText(path)

    def _submit(self):
        params = {}
        try:
            for key, (widget, definition) in self.fields.items():
                source = widget.editor if hasattr(widget, "editor") else widget
                if isinstance(source, QtWidgets.QComboBox): value = source.currentText()
                elif isinstance(source, QtWidgets.QCheckBox): value = source.isChecked()
                else: value = source.text().strip()
                if value == "" and key not in (): continue
                if definition.get("type") in ("array", "object", "integer"): value = json.loads(value)
                params[key] = value
            self.run_requested.emit(self.command, params)
        except (ValueError, json.JSONDecodeError) as error: QtWidgets.QMessageBox.warning(self, "参数错误", str(error))


class ColumnMappingDialog(QtWidgets.QDialog):
    ROLES = (("测试名称", True), ("验证点", True), ("步骤名称", False), ("步骤描述", False), ("预期结果", False), ("测试结果", True))

    def __init__(self, headers, suggested, parent=None):
        super().__init__(parent); self.setWindowTitle("确认 Excel 列含义"); self.resize(560, 420); self.selectors = {}
        layout = QtWidgets.QVBoxLayout(self); layout.addWidget(QtWidgets.QLabel("请确认每个字段对应的 Excel 列。带 * 的字段必须选择。"))
        form = QtWidgets.QFormLayout()
        for role, required in self.ROLES:
            selector = QtWidgets.QComboBox(); selector.addItem("不使用", None)
            for header in headers: selector.addItem(header, header)
            selected = suggested.get(role)
            if selected in headers: selector.setCurrentIndex(headers.index(selected) + 1)
            self.selectors[role] = (selector, required); form.addRow(f"{role}{' *' if required else ''}", selector)
        layout.addLayout(form); buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel | QtWidgets.QDialogButtonBox.StandardButton.Ok)
        buttons.rejected.connect(self.reject); buttons.accepted.connect(self._accept); layout.addWidget(buttons)

    def _accept(self):
        missing = [role for role, (selector, required) in self.selectors.items() if required and selector.currentData() is None]
        if missing: return QtWidgets.QMessageBox.warning(self, "映射不完整", f"请选择：{'、'.join(missing)}")
        self.accept()

    def mapping(self): return {role: selector.currentData() for role, (selector, _) in self.selectors.items() if selector.currentData()}


class RuntimeWorker(QtCore.QRunnable):
    class Signals(QtCore.QObject):
        finished = QtCore.Signal(object, object)
        failed = QtCore.Signal(str)

    def __init__(self, root, command, params):
        super().__init__(); self.root = root; self.command = command; self.params = params; self.signals = self.Signals()

    @QtCore.Slot()
    def run(self):
        runtime = None
        try:
            runtime = Runtime(self.root); task_id, result = runtime.run(self.command, self.params); self.signals.finished.emit(task_id, result)
        except Exception as error: self.signals.failed.emit(str(error))
        finally:
            if runtime is not None: runtime.close()


class EvidencePage(QtWidgets.QWidget):
    def __init__(self, window):
        super().__init__(); self.window = window; self.input_path = ""; self.items = []; self.mapping = {}; self.captures = {}; self.skipped = set(); self.current_row = 0; self.last_task = None
        self.temp_dir = QtCore.QTemporaryDir("testbox-evidence-XXXXXX"); self.temp = Path(self.temp_dir.path())
        layout = QtWidgets.QVBoxLayout(self); title = QtWidgets.QLabel("截图证据"); title.setObjectName("pageTitle"); layout.addWidget(title)
        subtitle = QtWidgets.QLabel("选择 Excel 用例，逐项截图并标注，然后生成 Word 证据报告。"); subtitle.setObjectName("muted"); layout.addWidget(subtitle)
        actions = QtWidgets.QHBoxLayout(); choose = QtWidgets.QPushButton("选择 Excel"); choose.clicked.connect(self.choose_excel); self.capture = QtWidgets.QPushButton("截图并标注 (F8)"); self.capture.setEnabled(False); self.capture.clicked.connect(self.take_screenshot)
        self.skip = QtWidgets.QPushButton("跳过"); self.skip.setEnabled(False); self.skip.clicked.connect(self.skip_current); self.end = QtWidgets.QPushButton("结束"); self.end.setEnabled(False); self.end.clicked.connect(self.end_session)
        actions.addWidget(choose); actions.addWidget(self.capture); actions.addWidget(self.skip); actions.addWidget(self.end); actions.addStretch(); layout.addLayout(actions)
        report_row = QtWidgets.QHBoxLayout(); report_row.addWidget(QtWidgets.QLabel("报告目录")); self.report_dir = QtWidgets.QLineEdit(); report_row.addWidget(self.report_dir, 1)
        choose_report_dir = QtWidgets.QToolButton(); choose_report_dir.setText("选择…"); choose_report_dir.clicked.connect(self.choose_report_dir); report_row.addWidget(choose_report_dir); layout.addLayout(report_row)
        self.current = QtWidgets.QLabel(""); self.current.setWordWrap(True); layout.addWidget(self.current)
        self.progress = QtWidgets.QLabel("尚未选择用例文件"); layout.addWidget(self.progress)
        self.table = QtWidgets.QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(["状态", "测试名称", "验证点", "步骤"]); self.table.horizontalHeader().setStretchLastSection(True); self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows); layout.addWidget(self.table, 1)
        footer = QtWidgets.QHBoxLayout(); self.commit_excel = QtWidgets.QCheckBox("生成后回写原 Excel"); self.commit_excel.setChecked(True); footer.addWidget(self.commit_excel)
        self.open_output = QtWidgets.QPushButton("打开输出目录"); self.open_output.setEnabled(False); self.open_output.clicked.connect(self.open_output_dir); footer.addWidget(self.open_output); footer.addStretch()
        self.build = QtWidgets.QPushButton("生成证据报告"); self.build.setObjectName("primary"); self.build.setEnabled(False); self.build.clicked.connect(self.build_reports); footer.addWidget(self.build); layout.addLayout(footer)

    def choose_excel(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择测试用例", filter="Excel (*.xlsx *.xlsm)")
        if not path: return
        params = {"input": path}; task_id, result = self.window.runtime.run("evidence.inspect", params)
        if result.status != "success" and result.data.get("error_code") == "COLUMN_MAPPING_REQUIRED":
            details = result.data.get("details", {}); dialog = ColumnMappingDialog(details.get("headers", []), details.get("suggested_mapping", {}), self)
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted: return
            params["column_mapping"] = dialog.mapping(); task_id, result = self.window.runtime.run("evidence.inspect", params)
        if result.status != "success": return self.window.show_result(task_id, result)
        self.input_path = path; self.items = result.data.get("items", []); self.mapping = result.data.get("mapping", {}); self.captures = {}; self.skipped = set(); self.current_row = 0; self.table.setRowCount(len(self.items))
        if not self.report_dir.text(): self.report_dir.setText(str(Path(path).parent / "word_output"))
        for row, item in enumerate(self.items):
            values = ("待截图", item["case_name"], item["checkpoint"], item.get("step_name") or item.get("step_description") or "-")
            for column, value in enumerate(values): self.table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
        self.capture.setEnabled(bool(self.items)); self.skip.setEnabled(bool(self.items)); self.end.setEnabled(bool(self.items)); self.build.setEnabled(False); self.open_output.setEnabled(False); self._update_progress(); self._show_current()

    def _update_progress(self): self.progress.setText(f"已截图 {len(self.captures)}，已跳过 {len(self.skipped)}，共 {len(self.items)} 条")

    def choose_report_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择 Word 报告目录", self.report_dir.text())
        if path: self.report_dir.setText(path)

    def _show_current(self):
        if not self.items or self.current_row >= len(self.items): self.current.setText("当前执行已结束"); return
        item = self.items[self.current_row]; step = item.get("step_name") or item.get("step_description") or ""
        self.current.setText(f"当前：{item['case_name']}\n验证点：{item['checkpoint']}" + (f"\n步骤：{step}" if step else "")); self.table.selectRow(self.current_row)

    def _advance(self):
        for row in range(self.current_row + 1, len(self.items)):
            if row not in self.captures and row not in self.skipped: self.current_row = row; self._show_current(); return
        self.current_row = len(self.items); self._show_current(); self.capture.setEnabled(False); self.skip.setEnabled(False)

    def skip_current(self):
        if self.current_row >= len(self.items): return
        self.skipped.add(self.current_row); self.table.item(self.current_row, 0).setText("已跳过"); self._update_progress(); self._advance()

    def end_session(self):
        self.capture.setEnabled(False); self.skip.setEnabled(False); self.end.setEnabled(False); self.current.setText("执行已结束，已保存的截图仍可生成报告。")

    def take_screenshot(self):
        self.window.hide(); QtCore.QTimer.singleShot(350, self._capture_now)

    def _capture_now(self):
        screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos()) or QtGui.QGuiApplication.primaryScreen(); pixmap = screen.grabWindow(0); self.window.show(); self.window.raise_()
        if pixmap.isNull(): return QtWidgets.QMessageBox.warning(self, "截图失败", "未能读取屏幕，请检查系统屏幕录制权限。")
        dialog = AnnotationDialog(pixmap, self.window)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted: return
        item = self.items[self.current_row]; path = self.temp / f"shot-row-{item['row_index']}.png"
        if not dialog.result_pixmap().save(str(path), "PNG"): return QtWidgets.QMessageBox.warning(self, "保存失败", "无法保存截图临时文件。")
        self.captures[self.current_row] = str(path); self.table.item(self.current_row, 0).setText("已截图"); self._update_progress(); self.build.setEnabled(True); self._advance()

    def build_reports(self):
        ordered = sorted(self.captures); screenshots = [self.captures[row] for row in ordered]; row_indexes = [self.items[row]["row_index"] for row in ordered]
        report_dir = Path(self.report_dir.text()).expanduser(); existing = [str(path) for path in sorted(report_dir.glob("*.docx"))] if report_dir.is_dir() else []
        params = {"input": self.input_path, "screenshots": screenshots, "row_indexes": row_indexes, "column_mapping": self.mapping, "update_excel": True}
        if existing: params["existing_reports"] = existing
        self.build.setEnabled(False); self.capture.setEnabled(False); self.progress.setText("正在生成报告…")
        self.window.run_async("evidence.build", params, self._build_finished)

    def _build_finished(self, task_id, result):
        self.last_task = task_id
        if result.status == "success" and self.commit_excel.isChecked():
            excel_output = next((name for name in result.files if name.startswith("executed-") and name.lower().endswith((".xlsx", ".xlsm"))), None)
            if excel_output:
                try: self.window.runtime.commit_output(task_id, excel_output, Path(self.input_path))
                except (ValueError, LookupError, OSError) as error: result.warnings.append(f"原 Excel 回写失败：{error}")
        if result.status == "success":
            report_dir = Path(self.report_dir.text()).expanduser()
            for report in (name for name in result.files if name.startswith("reports/") and name.endswith(".docx")):
                try: self.window.runtime.commit_output(task_id, report, report_dir / Path(report).name)
                except (ValueError, LookupError, OSError) as error: result.warnings.append(f"Word 报告导出失败：{error}")
        self.open_output.setEnabled(result.status == "success"); self.window.show_result(task_id, result)
        self.build.setEnabled(bool(self.captures)); self.capture.setEnabled(self.current_row < len(self.items))

    def open_output_dir(self):
        if self.last_task: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.window.runtime.workspace_dir / self.last_task / "output")))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__(); self.runtime = Runtime(); self.setWindowTitle("TestBox"); self.resize(1180, 760)
        shell = QtWidgets.QWidget(); self.setCentralWidget(shell); layout = QtWidgets.QHBoxLayout(shell); layout.setContentsMargins(0, 0, 0, 0)
        nav = QtWidgets.QListWidget(); nav.setFixedWidth(240); nav.setObjectName("nav"); self.stack = QtWidgets.QStackedWidget(); layout.addWidget(nav); layout.addWidget(self.stack, 1)
        if "evidence.inspect" in self.runtime.manager.available:
            nav.addItem("截图证据"); self.evidence_page = EvidencePage(self); self.stack.addWidget(self.evidence_page)
        for command, manifest in sorted(self.runtime.manager.available.items()):
            nav.addItem(command); page = SchemaForm(command, manifest); page.run_requested.connect(self.run_command); self.stack.addWidget(page)
        nav.currentRowChanged.connect(self.stack.setCurrentIndex); nav.setCurrentRow(0)
        if hasattr(self, "evidence_page"): QtGui.QShortcut(QtGui.QKeySequence("F8"), self, activated=lambda: self.evidence_page.take_screenshot() if self.evidence_page.capture.isEnabled() else None)
        self.statusBar().showMessage(f"已加载 {len(self.runtime.manager.available)} 个命令")

    def run_command(self, command, params):
        self.run_async(command, params, self.show_result)

    def run_async(self, command, params, callback):
        worker = RuntimeWorker(self.runtime.root, command, params)
        worker.signals.finished.connect(callback); worker.signals.failed.connect(lambda message: QtWidgets.QMessageBox.warning(self, "无法运行", message))
        QtCore.QThreadPool.globalInstance().start(worker)

    def show_result(self, task_id, result):
        path = self.runtime.workspace_dir / task_id
        box = QtWidgets.QMessageBox(self); box.setWindowTitle("任务完成" if result.status == "success" else "任务失败"); box.setText(result.message); box.setInformativeText(f"任务 ID：{task_id}\n工作区：{path}"); box.setDetailedText(json.dumps(result.to_dict(), ensure_ascii=False, indent=2)); box.exec()

    def closeEvent(self, event): self.runtime.close(); super().closeEvent(event)


STYLE = """
QWidget { font-size: 14px; color: #17202a; }
QMainWindow, QStackedWidget { background: #f7f8fa; }
#nav { background: #20252b; color: #f6f7f8; border: 0; padding: 12px 8px; outline: 0; }
#nav::item { min-height: 38px; padding: 0 12px; border-radius: 6px; }
#nav::item:selected { background: #356ae6; }
#pageTitle { font-size: 24px; font-weight: 650; padding-top: 20px; }
#muted { color: #667085; padding-bottom: 16px; }
QStackedWidget > QWidget { padding: 24px 32px; }
QLineEdit, QComboBox { min-height: 34px; border: 1px solid #cfd4dc; border-radius: 5px; padding: 0 8px; background: white; }
QPushButton { min-height: 34px; padding: 0 14px; border: 1px solid #c5cad3; border-radius: 5px; background: white; }
QPushButton:hover { background: #eef2f8; } QPushButton:disabled { color: #9aa1ab; background: #eef0f2; }
#primary { background: #356ae6; color: white; border-color: #356ae6; font-weight: 600; }
QTableWidget { background: white; border: 1px solid #d9dde4; gridline-color: #e8eaee; }
QHeaderView::section { background: #eef1f5; padding: 8px; border: 0; border-bottom: 1px solid #d9dde4; }
"""


def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv); app.setApplicationName("TestBox"); app.setStyleSheet(STYLE)
    window = MainWindow(); window.show(); raise SystemExit(app.exec())


if __name__ == "__main__": main()
