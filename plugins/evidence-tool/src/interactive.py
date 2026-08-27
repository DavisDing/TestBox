"""Interactive evidence capture UI.

The interactive host intentionally mirrors the source screenshot-to-word desktop
flow: a compact floating execution panel, a light card-based mapping dialog,
and a stable QWidget/QImage annotation canvas.  Keeping this UI in the plugin
host means the TestBox shell never appears in captured screenshots.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Callable, List, Optional

ROLES = ("测试名称", "验证点", "步骤名称", "步骤描述", "预期结果", "测试结果")
REQUIRED = ("测试名称", "验证点", "测试结果")


SOURCE_STYLE = """
* { font-family: "Microsoft YaHei", "PingFang SC", "Inter", "Segoe UI", sans-serif; font-size: 13px; }
QDialog, QWidget { background: #f4f7fb; color: #172033; }
QLabel#PageTitle { color: #111827; font-size: 23px; font-weight: 800; }
QLabel#SectionTitle { color: #111827; font-size: 16px; font-weight: 700; }
QLabel#MutedLabel, QLabel#PanelTip { color: #64748b; }
QLabel#PanelTitle { color: #0f172a; font-size: 18px; font-weight: 800; }
QLabel#PanelKey { color: #64748b; font-size: 12px; font-weight: 700; min-width: 42px; }
QLabel#PanelValue { color: #111827; font-size: 13px; }
QFrame#Card, QFrame#FloatingCard { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 16px; }
QFrame#MappingRow { background: #f8fafc; border: 1px solid #edf2f7; border-radius: 10px; }
QFrame#MappingRow:hover { background: #f1f7ff; border-color: #bfdbfe; }
QLabel#MappingField { color: #0f172a; font-weight: 700; }
QLabel#InlineHint { color: #64748b; background: #f8fafc; border: 1px dashed #dbe3ef; border-radius: 10px; padding: 8px 10px; }
QPushButton { border: none; border-radius: 10px; padding: 9px 14px; background: #e5e7eb; color: #111827; font-weight: 700; }
QPushButton:hover { background: #d1d5db; }
QPushButton:disabled { background: #eef2f7; color: #94a3b8; }
QPushButton#PrimaryButton { background: #2563eb; color: #ffffff; }
QPushButton#PrimaryButton:hover { background: #1d4ed8; }
QPushButton#DangerButton { background: #fee2e2; color: #b91c1c; }
QPushButton#DangerButton:hover { background: #fecaca; }
QComboBox { background: #ffffff; border: 1px solid #d7dee9; border-radius: 8px; padding: 6px 10px; min-height: 22px; color: #111827; }
QComboBox:hover { border-color: #93c5fd; background: #f8fbff; }
QComboBox:focus { border: 1px solid #2563eb; }
QComboBox QAbstractItemView { background: #ffffff; border: 1px solid #d7dee9; selection-background-color: #eef6ff; selection-color: #1d4ed8; }
QTableWidget { background: #ffffff; border: 1px solid #dbe3ef; border-radius: 10px; gridline-color: #edf2f7; alternate-background-color: #f8fafc; }
QHeaderView::section { background: #f8fafc; padding: 8px; border: none; border-bottom: 1px solid #e5e7eb; font-weight: 700; color: #334155; }
QProgressBar { background: #e5e7eb; border-radius: 7px; height: 10px; text-align: center; color: #475569; }
QProgressBar::chunk { background: #2563eb; border-radius: 7px; }
QScrollArea { background: #eef3f8; border: 1px solid #dbe3ef; border-radius: 12px; }
QToolButton { border: 1px solid #dbe3ef; border-radius: 9px; padding: 7px 10px; background: #ffffff; color: #111827; }
QToolButton:hover { background: #f1f5f9; }
QToolButton:checked { background: #dbeafe; border-color: #2563eb; color: #1d4ed8; }
"""


def _app(QtWidgets):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(SOURCE_STYLE)
    return app


def confirm_mapping(preview: dict, logger) -> dict[str, str | None] | None:
    try:
        from PySide6 import QtCore, QtWidgets
    except ModuleNotFoundError as error:
        raise RuntimeError("交互截图模式需要 PySide6；无桌面环境时请安装插件 requirements.txt") from error

    _app(QtWidgets)
    columns = preview.get("columns", [])
    headers = [str(column["header"]) for column in columns]
    suggested = preview.get("suggested_mapping", {})
    reasons = preview.get("mapping_reasons", {})

    class MappingDialog(QtWidgets.QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("确认 Excel 列含义")
            self.resize(980, 600)
            self.combos: dict[str, QtWidgets.QComboBox] = {}
            root = QtWidgets.QVBoxLayout(self)
            root.setContentsMargins(18, 16, 18, 16)
            root.setSpacing(12)
            title = QtWidgets.QLabel("确认 Excel 列含义")
            title.setObjectName("PageTitle")
            root.addWidget(title)
            subtitle = QtWidgets.QLabel(
                f"工作表：{preview.get('sheet', '')}    表头行：第 {preview.get('header_row', 1)} 行\n"
                "系统已自动识别列含义；请核对后确认，必要时可以手动调整。"
            )
            subtitle.setObjectName("MutedLabel")
            subtitle.setWordWrap(True)
            root.addWidget(subtitle)
            body = QtWidgets.QHBoxLayout()
            body.setSpacing(12)
            mapping_box = QtWidgets.QFrame()
            mapping_box.setObjectName("Card")
            mapping_layout = QtWidgets.QVBoxLayout(mapping_box)
            mapping_layout.setContentsMargins(14, 12, 14, 12)
            mapping_title = QtWidgets.QLabel("字段映射（* 必填）")
            mapping_title.setObjectName("SectionTitle")
            mapping_layout.addWidget(mapping_title)
            options = ["不使用", *headers]
            for role in ROLES:
                row = QtWidgets.QFrame()
                row.setObjectName("MappingRow")
                row_layout = QtWidgets.QHBoxLayout(row)
                row_layout.setContentsMargins(10, 7, 10, 7)
                label = QtWidgets.QLabel(role + (" *" if role in REQUIRED else ""))
                label.setObjectName("MappingField")
                label.setFixedWidth(76)
                combo = QtWidgets.QComboBox()
                combo.addItems(options)
                match = suggested.get(role)
                if match in headers:
                    combo.setCurrentText(match)
                combo.setToolTip(reasons.get(role, ""))
                row_layout.addWidget(label)
                row_layout.addWidget(combo, 1)
                mapping_layout.addWidget(row)
                self.combos[role] = combo
            hint = QtWidgets.QLabel("鼠标移动到下拉框可查看自动识别依据。")
            hint.setObjectName("InlineHint")
            hint.setWordWrap(True)
            mapping_layout.addWidget(hint)
            mapping_layout.addStretch(1)
            body.addWidget(mapping_box, 2)

            table_box = QtWidgets.QFrame()
            table_box.setObjectName("Card")
            table_layout = QtWidgets.QVBoxLayout(table_box)
            table_layout.setContentsMargins(16, 14, 16, 14)
            table_title = QtWidgets.QLabel("列预览与样例值")
            table_title.setObjectName("SectionTitle")
            table_layout.addWidget(table_title)
            table = QtWidgets.QTableWidget(len(columns), 3)
            table.setHorizontalHeaderLabels(["序号", "列名", "样例值"])
            table.verticalHeader().setVisible(False)
            table.setAlternatingRowColors(True)
            table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
            for row, column in enumerate(columns):
                table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(column["index"])))
                table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(column["header"])))
                table.setItem(row, 2, QtWidgets.QTableWidgetItem(" / ".join(column.get("samples", []))))
            table_layout.addWidget(table)
            body.addWidget(table_box, 5)
            root.addLayout(body, 1)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("确认")
            buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
            buttons.accepted.connect(self._accept_mapping)
            buttons.rejected.connect(self.reject)
            root.addWidget(buttons)

        def mapping(self):
            return {role: (None if combo.currentText() == "不使用" else combo.currentText()) for role, combo in self.combos.items()}

        def _accept_mapping(self):
            missing = [role for role in REQUIRED if not self.mapping().get(role)]
            if missing:
                QtWidgets.QMessageBox.warning(self, "缺少必要列", f"请确认这些必要列：{'、'.join(missing)}")
                return
            self.accept()

    dialog = MappingDialog()
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        logger.info("用户取消 Excel 列映射")
        return None
    logger.info("Excel 列映射已确认")
    return dialog.mapping()


class Annotation:
    """One annotation drawn on the screenshot canvas."""

    __slots__ = ("kind", "data")

    def __init__(self, kind: str, data: object):
        self.kind = kind
        self.data = data


def run_session(items, output_dir: Path, logger, on_saved: Callable[[object, Path], bool] | None = None) -> dict:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ModuleNotFoundError as error:
        raise RuntimeError("交互截图模式需要 PySide6；无桌面环境时请改用 screenshots 批处理模式") from error

    _app(QtWidgets)
    staging = Path(output_dir) / ".staging" / "screenshots"
    staging.mkdir(parents=True, exist_ok=True)
    results: list[tuple[object, Path]] = []
    skipped: list[object] = []

    class AnnotationCanvas(QtWidgets.QWidget):
        zoom_changed = QtCore.Signal(float)

        def __init__(self, image_path: Path, parent=None):
            super().__init__(parent)
            self.image_path = Path(image_path)
            self.pixmap = QtGui.QPixmap(str(image_path))
            self.image = QtGui.QImage(str(image_path))
            if self.pixmap.isNull() or self.image.isNull():
                raise ValueError(f"无法打开截图：{image_path}")
            self.scale_factor = 1.0
            self.setFixedSize(self.pixmap.size())
            self.setMouseTracking(True)
            self.tool = "rect"
            self.annotations: List[Annotation] = []
            self.crop_rect: Optional[QtCore.QRect] = None
            self._drag_start = None
            self._drag_current = None
            self._current_path = []

        def set_tool(self, tool: str): self.tool = tool
        def set_scale(self, scale: float):
            self.scale_factor = max(0.12, min(scale, 3.0))
            self.setFixedSize(QtCore.QSize(max(1, round(self.pixmap.width() * self.scale_factor)), max(1, round(self.pixmap.height() * self.scale_factor))))
            self.update(); self.zoom_changed.emit(self.scale_factor)
        def fit_to(self, size):
            if self.pixmap.width() and self.pixmap.height():
                self.set_scale(min(max(0.12, (size.width() - 24) / self.pixmap.width()), max(0.12, (size.height() - 24) / self.pixmap.height()), 1.0))
        def undo(self):
            if self.tool == "crop" and self.crop_rect is not None: self.crop_rect = None
            elif self.annotations: self.annotations.pop()
            self.update()

        def paintEvent(self, event):
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.scale(self.scale_factor, self.scale_factor)
            painter.drawPixmap(0, 0, self.pixmap)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            for annotation in self.annotations: self._draw_annotation(painter, annotation)
            self._draw_active_shape(painter)
            if self.crop_rect and self.crop_rect.isValid(): self._draw_crop_overlay(painter, self.crop_rect)

        def _to_image_point(self, point):
            x = round(point.x() / self.scale_factor); y = round(point.y() / self.scale_factor)
            return QtCore.QPoint(max(0, min(x, self.image.width() - 1)), max(0, min(y, self.image.height() - 1)))
        def mousePressEvent(self, event):
            if event.button() != QtCore.Qt.MouseButton.LeftButton: return
            point = self._to_image_point(event.position().toPoint())
            if self.tool == "text":
                text, ok = QtWidgets.QInputDialog.getText(self, "添加文字", "请输入标注文字：")
                if ok and text.strip(): self.annotations.append(Annotation("text", (point, text.strip()))); self.update()
                return
            self._drag_start = point; self._drag_current = point; self._current_path = [point]
        def mouseMoveEvent(self, event):
            if not self._drag_start: return
            self._drag_current = self._to_image_point(event.position().toPoint())
            if self.tool in {"pen", "highlighter"}: self._current_path.append(self._drag_current)
            self.update()
        def mouseReleaseEvent(self, event):
            if event.button() != QtCore.Qt.MouseButton.LeftButton or not self._drag_start: return
            end = self._to_image_point(event.position().toPoint()); rect = QtCore.QRect(self._drag_start, end).normalized(); bounds = QtCore.QRect(0, 0, self.image.width(), self.image.height())
            if self.tool == "crop" and rect.width() >= 20 and rect.height() >= 20: self.crop_rect = rect.intersected(bounds)
            elif self.tool in {"rect", "ellipse"} and rect.width() >= 6 and rect.height() >= 6: self.annotations.append(Annotation(self.tool, rect.intersected(bounds)))
            elif self.tool in {"pen", "highlighter"} and len(self._current_path) >= 2: self.annotations.append(Annotation(self.tool, list(self._current_path)))
            self._drag_start = self._drag_current = None; self._current_path = []; self.update()
        def _draw_polyline(self, painter, points, highlighter=False):
            if len(points) < 2: return
            color = QtGui.QColor(250, 204, 21, 105) if highlighter else QtGui.QColor("#ef4444"); width = 18 if highlighter else 4
            painter.setPen(QtGui.QPen(color, width, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin))
            for a, b in zip(points, points[1:]): painter.drawLine(a, b)
        def _draw_annotation(self, painter, annotation):
            if annotation.kind == "rect": painter.setPen(QtGui.QPen(QtGui.QColor("#ef4444"), 4)); painter.setBrush(QtCore.Qt.BrushStyle.NoBrush); painter.drawRect(annotation.data)
            elif annotation.kind == "ellipse": painter.setPen(QtGui.QPen(QtGui.QColor("#ef4444"), 4)); painter.setBrush(QtCore.Qt.BrushStyle.NoBrush); painter.drawEllipse(annotation.data)
            elif annotation.kind in {"pen", "highlighter"}: self._draw_polyline(painter, annotation.data, annotation.kind == "highlighter")
            elif annotation.kind == "text":
                point, text = annotation.data; painter.setPen(QtGui.QPen(QtGui.QColor("#2563eb"), 1)); font = QtGui.QFont(); font.setPointSize(18); font.setBold(True); painter.setFont(font); painter.drawText(point, text)
        def _draw_active_shape(self, painter):
            if not self._drag_start or not self._drag_current: return
            rect = QtCore.QRect(self._drag_start, self._drag_current).normalized()
            if self.tool in {"rect", "crop"}: painter.setPen(QtGui.QPen(QtGui.QColor("#2563eb") if self.tool == "crop" else QtGui.QColor("#ef4444"), 3, QtCore.Qt.PenStyle.DashLine)); painter.setBrush(QtCore.Qt.BrushStyle.NoBrush); painter.drawRect(rect)
            elif self.tool == "ellipse": painter.setPen(QtGui.QPen(QtGui.QColor("#ef4444"), 3, QtCore.Qt.PenStyle.DashLine)); painter.setBrush(QtCore.Qt.BrushStyle.NoBrush); painter.drawEllipse(rect)
            elif self.tool in {"pen", "highlighter"}: self._draw_polyline(painter, self._current_path, self.tool == "highlighter")
        def _draw_crop_overlay(self, painter, rect):
            overlay = QtGui.QColor(15, 23, 42, 95); full = QtCore.QRect(0, 0, self.image.width(), self.image.height())
            painter.fillRect(0, 0, full.width(), rect.top(), overlay); painter.fillRect(0, rect.bottom(), full.width(), full.height() - rect.bottom(), overlay); painter.fillRect(0, rect.top(), rect.left(), rect.height(), overlay); painter.fillRect(rect.right(), rect.top(), full.width() - rect.right(), rect.height(), overlay)
            painter.setPen(QtGui.QPen(QtGui.QColor("#2563eb"), 3)); painter.setBrush(QtCore.Qt.BrushStyle.NoBrush); painter.drawRect(rect)
        def _intersects(self, annotation, rect):
            if annotation.kind in {"rect", "ellipse"}: return annotation.data.intersects(rect)
            if annotation.kind in {"pen", "highlighter"}: return any(rect.contains(p) for p in annotation.data)
            if annotation.kind == "text": return rect.contains(annotation.data[0])
            return True
        def save_to(self, output_path: Path | None = None) -> Path:
            output_path = Path(output_path or self.image_path)
            crop = self.crop_rect.intersected(self.image.rect()) if self.crop_rect else QtCore.QRect(0, 0, self.image.width(), self.image.height())
            if not crop.isValid() or crop.width() <= 0 or crop.height() <= 0: crop = QtCore.QRect(0, 0, self.image.width(), self.image.height())
            final_image = self.image.copy(crop); painter = QtGui.QPainter(final_image); painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True); painter.translate(-crop.x(), -crop.y())
            for annotation in self.annotations:
                if self._intersects(annotation, crop): self._draw_annotation(painter, annotation)
            painter.end()
            if not final_image.save(str(output_path), "PNG"): raise IOError(f"保存图片失败：{output_path}")
            return output_path

    class Annotator(QtWidgets.QDialog):
        def __init__(self, path: Path):
            super().__init__(); self.setWindowTitle("截图标注"); self.resize(1100, 760); self.result_path: Path | None = None; self.canvas = AnnotationCanvas(path, self); self.auto_fit = True
            root = QtWidgets.QVBoxLayout(self); root.setContentsMargins(10, 10, 10, 10); root.setSpacing(8)
            toolbar = QtWidgets.QHBoxLayout(); toolbar.addWidget(QtWidgets.QLabel("工具：")); group = QtWidgets.QButtonGroup(self); group.setExclusive(True)
            for key, label in (("rect", "方形"), ("ellipse", "圆形"), ("highlighter", "荧光笔"), ("pen", "画笔"), ("text", "文字"), ("crop", "裁剪")):
                button = QtWidgets.QToolButton(); button.setText(label); button.setCheckable(True); button.setChecked(key == "rect"); button.clicked.connect(lambda checked=False, tool=key: self.canvas.set_tool(tool)); group.addButton(button); toolbar.addWidget(button)
            toolbar.addSpacing(8); fit = QtWidgets.QPushButton("适配"); fit.clicked.connect(self.fit_to_window); actual = QtWidgets.QPushButton("100%"); actual.clicked.connect(lambda: self.set_zoom(1.0, False)); minus = QtWidgets.QPushButton("-"); minus.clicked.connect(lambda: self.set_zoom(self.canvas.scale_factor * .85, False)); plus = QtWidgets.QPushButton("+"); plus.clicked.connect(lambda: self.set_zoom(self.canvas.scale_factor * 1.15, False)); self.zoom = QtWidgets.QLabel("100%"); self.zoom.setObjectName("MutedLabel")
            for widget in (fit, actual, minus, plus, self.zoom): toolbar.addWidget(widget)
            toolbar.addStretch(1); toolbar.addWidget(QtWidgets.QLabel("Ctrl+Z 撤销，Ctrl+S 保存，Esc 取消")); undo = QtWidgets.QPushButton("撤销"); undo.clicked.connect(self.canvas.undo); cancel = QtWidgets.QPushButton("取消"); cancel.setObjectName("DangerButton"); cancel.clicked.connect(self.reject); save = QtWidgets.QPushButton("保存"); save.setObjectName("PrimaryButton"); save.clicked.connect(self._save)
            toolbar.addWidget(undo); toolbar.addWidget(save); toolbar.addWidget(cancel); root.addLayout(toolbar)
            self.scroll = QtWidgets.QScrollArea(); self.scroll.setWidget(self.canvas); self.scroll.setWidgetResizable(False); self.scroll.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter); root.addWidget(self.scroll, 1); self.canvas.zoom_changed.connect(lambda scale: self.zoom.setText(f"{round(scale * 100)}%"))
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Z"), self, activated=self.canvas.undo); QtGui.QShortcut(QtGui.QKeySequence("Ctrl+S"), self, activated=self._save); QtGui.QShortcut(QtGui.QKeySequence("Escape"), self, activated=self.reject)
        def showEvent(self, event):
            super().showEvent(event)
            if self.auto_fit: self.fit_to_window()
        def resizeEvent(self, event):
            super().resizeEvent(event)
            if self.auto_fit and hasattr(self, "scroll"): self.fit_to_window()
        def fit_to_window(self): self.auto_fit = True; self.canvas.fit_to(self.scroll.viewport().size())
        def set_zoom(self, scale, auto=False): self.auto_fit = auto; self.canvas.set_scale(scale)
        def _save(self):
            try: self.result_path = self.canvas.save_to()
            except Exception as error: QtWidgets.QMessageBox.critical(self, "保存失败", str(error)); return
            self.accept()

    class Session(QtWidgets.QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("执行控制")
            self.resize(420, 440)
            self.setMinimumSize(380, 360)
            self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
            self.setWindowOpacity(0.96)
            self.index = 0
            self.current_saved = False
            self.capture_pending = False
            self.session_active = True
            self.field_rows = {}
            root = QtWidgets.QVBoxLayout(self); root.setContentsMargins(12, 12, 12, 12); root.setSpacing(9)
            header = QtWidgets.QFrame(); header.setObjectName("FloatingCard"); hl = QtWidgets.QVBoxLayout(header); hl.setContentsMargins(12, 10, 12, 10); title_row = QtWidgets.QHBoxLayout(); title = QtWidgets.QLabel("执行控制"); title.setObjectName("PanelTitle"); self.progress_label = QtWidgets.QLabel("-"); self.progress_label.setObjectName("MutedLabel"); title_row.addWidget(title); title_row.addStretch(1); title_row.addWidget(self.progress_label); self.progress = QtWidgets.QProgressBar(); hl.addLayout(title_row); hl.addWidget(self.progress); root.addWidget(header)
            card = QtWidgets.QFrame(); card.setObjectName("FloatingCard"); fields = QtWidgets.QVBoxLayout(card); fields.setContentsMargins(12, 10, 12, 10); fields.setSpacing(6)
            for key, label in (("case", "用例"), ("checkpoint", "验证"), ("step", "步骤"), ("desc", "描述"), ("expected", "预期")):
                row = QtWidgets.QHBoxLayout(); key_label = QtWidgets.QLabel(label); key_label.setObjectName("PanelKey"); value = QtWidgets.QLabel("-"); value.setObjectName("PanelValue"); value.setWordWrap(True); row.addWidget(key_label); row.addWidget(value, 1); fields.addLayout(row); self.field_rows[key] = (key_label, value)
            root.addWidget(card, 1)
            actions = QtWidgets.QHBoxLayout(); self.capture = QtWidgets.QPushButton("截图并标注 F8"); self.capture.setObjectName("PrimaryButton"); self.capture.clicked.connect(self.capture_item); self.next = QtWidgets.QPushButton("下一条"); self.next.clicked.connect(self.next_item); actions.addWidget(self.capture, 2); actions.addWidget(self.next, 1); root.addLayout(actions)
            actions2 = QtWidgets.QHBoxLayout(); self.skip = QtWidgets.QPushButton("跳过"); self.skip.clicked.connect(self.skip_item); self.finish = QtWidgets.QPushButton("结束"); self.finish.setObjectName("DangerButton"); self.finish.clicked.connect(self.reject); actions2.addWidget(self.skip); actions2.addWidget(self.finish); root.addLayout(actions2)
            tip = QtWidgets.QLabel("截图时执行面板会隐藏；保存标注后会立即写入 Word 并回写 Excel。\n快捷键：F8 截图，Ctrl+Z 撤销标注。"); tip.setObjectName("PanelTip"); tip.setWordWrap(True); root.addWidget(tip); QtGui.QShortcut(QtGui.QKeySequence("F8"), self, activated=self.capture_item); self.refresh()
        def refresh(self):
            if self.index >= len(items): self.accept(); return
            item = items[self.index]; self.current_saved = False; self.next.setEnabled(False); total = len(items); self.progress.setRange(0, total); self.progress.setValue(self.index); self.progress_label.setText(f"{self.index + 1}/{total}")
            values = {"case": item.case_name, "checkpoint": item.checkpoint, "step": item.step_name, "desc": item.step_description, "expected": item.expected_result}
            for key, (_label, widget) in self.field_rows.items(): widget.setText(values.get(key) or "-"); widget.parentWidget().setVisible(key in {"case", "checkpoint"} or bool(values.get(key)))
        def keyPressEvent(self, event):
            if event.key() == QtCore.Qt.Key.Key_F8: self.capture_item(); return
            super().keyPressEvent(event)
        def skip_item(self):
            if self.index < len(items): skipped.append(items[self.index]); logger.info(f"跳过：第 {items[self.index].row_index} 行")
            self.index += 1; self.refresh()
        def next_item(self):
            if not self.current_saved: QtWidgets.QMessageBox.information(self, "提示", "请先截图并保存标注。"); return
            self.index += 1; self.refresh()
        def _restore(self):
            self.capture_pending = False
            self.setWindowOpacity(0.96)
            self.showNormal()
            self.show()
            self.raise_()
            self.activateWindow()
            self.capture.setEnabled(True)

        def capture_item(self):
            if self.capture_pending or self.index >= len(items):
                return
            item = items[self.index]
            self.capture_pending = True
            self.capture.setEnabled(False)
            # Do not call hide() on a modal QDialog: on some Qt/macOS builds it
            # ends exec() immediately, which made the host return while the
            # delayed screenshot callback was still pending.  A fully transparent
            # dialog keeps the modal event loop alive and is omitted from the
            # composited desktop capture.
            self.setWindowOpacity(0.0)
            self.show()
            QtWidgets.QApplication.processEvents()
            QtCore.QTimer.singleShot(500, lambda: self.capture_after_hide(item))
        def capture_after_hide(self, item):
            if not self.session_active:
                return
            path = staging / f"row-{item.row_index}-{_dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
            try:
                screen = QtGui.QGuiApplication.primaryScreen()
                if screen is None: raise RuntimeError("未检测到主屏幕")
                QtWidgets.QApplication.processEvents()
                pixmap = screen.grabWindow(0)
                if pixmap.isNull() or not pixmap.save(str(path), "PNG"): raise RuntimeError("系统未返回有效截图，请检查屏幕录制权限")
                logger.info(f"截图已保存到临时文件：第 {item.row_index} 行")
                annotator = Annotator(path)
                try:
                    accepted = annotator.exec() == QtWidgets.QDialog.DialogCode.Accepted
                    result_path = annotator.result_path
                finally:
                    annotator.deleteLater()
                if not accepted or result_path is None:
                    logger.info(f"取消标注：第 {item.row_index} 行"); self._restore(); return
                if on_saved is not None and not on_saved(item, result_path): raise RuntimeError("当前证据保存未完成")
                results.append((item, result_path)); self.current_saved = True; logger.info(f"截图证据已保存：第 {item.row_index} 行"); self._restore(); self.next.setEnabled(True)
            except Exception as error:
                logger.error(f"截图或标注失败：{type(error).__name__}: {error}")
                self._restore()
                QtWidgets.QMessageBox.critical(self, "截图/标注失败", str(error))

        def reject(self):
            self.session_active = False
            super().reject()

        def accept(self):
            self.session_active = False
            super().accept()

    session = Session()
    accepted = session.exec() == QtWidgets.QDialog.DialogCode.Accepted
    ended = not accepted and session.index < len(items)
    return {"saved": results, "skipped": skipped, "ended": ended}
