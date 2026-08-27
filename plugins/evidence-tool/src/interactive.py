"""Qt interaction copied from screenshot-to-word's execution flow.

The plugin host owns this window sequence:
1. confirm Excel column mapping;
2. show one pending case/step at a time;
3. hide all TestBox/evidence windows before F8 capture;
4. annotate and save;
5. persist the Word/Excel result before enabling “下一条”.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Callable

ROLES = ("测试名称", "验证点", "步骤名称", "步骤描述", "预期结果", "测试结果")
REQUIRED = ("测试名称", "验证点", "测试结果")


def _app(QtWidgets):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def confirm_mapping(preview: dict, logger) -> dict[str, str | None] | None:
    """Show the same mapping confirmation step as the source desktop app."""
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
            self.resize(940, 580)
            self.combos: dict[str, QtWidgets.QComboBox] = {}
            root = QtWidgets.QVBoxLayout(self)
            title = QtWidgets.QLabel("确认 Excel 列含义")
            title.setStyleSheet("font-size:18px;font-weight:700")
            root.addWidget(title)
            subtitle = QtWidgets.QLabel(
                f"工作表：{preview.get('sheet', '')}    表头行：第 {preview.get('header_row', 1)} 行\n"
                "系统已自动识别列含义；请核对后确认，必要时可以手动调整。"
            )
            subtitle.setWordWrap(True)
            root.addWidget(subtitle)
            body = QtWidgets.QHBoxLayout()
            mapping_box = QtWidgets.QGroupBox("字段映射（* 为必填）")
            mapping_layout = QtWidgets.QFormLayout(mapping_box)
            options = ["不使用", *headers]
            for role in ROLES:
                combo = QtWidgets.QComboBox()
                combo.addItems(options)
                match = suggested.get(role)
                if match in headers:
                    combo.setCurrentText(match)
                label = f"{role} *" if role in REQUIRED else role
                combo.setToolTip(reasons.get(role, ""))
                mapping_layout.addRow(label, combo)
                self.combos[role] = combo
            body.addWidget(mapping_box, stretch=2)
            table = QtWidgets.QTableWidget(len(columns), 3)
            table.setHorizontalHeaderLabels(["序号", "列名", "样例值"])
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            table.horizontalHeader().setStretchLastSection(True)
            for row, column in enumerate(columns):
                table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(column["index"])))
                table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(column["header"])))
                table.setItem(row, 2, QtWidgets.QTableWidgetItem(" / ".join(column.get("samples", []))))
            body.addWidget(table, stretch=5)
            root.addLayout(body, stretch=1)
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
            )
            buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("确认")
            buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
            buttons.accepted.connect(self._accept_mapping)
            buttons.rejected.connect(self.reject)
            root.addWidget(buttons)

        def mapping(self) -> dict[str, str | None]:
            return {
                role: (None if combo.currentText() == "不使用" else combo.currentText())
                for role, combo in self.combos.items()
            }

        def _accept_mapping(self):
            mapping = self.mapping()
            missing = [role for role in REQUIRED if not mapping.get(role)]
            if missing:
                QtWidgets.QMessageBox.warning(self, "缺少必要列", f"请确认这些必要列：{'、'.join(missing)}")
                return
            self.accept()

    dialog = MappingDialog()
    if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        logger.info("用户取消 Excel 列映射")
        return None
    result = dialog.mapping()
    logger.info("Excel 列映射已确认")
    return result


def run_session(
    items,
    output_dir: Path,
    logger,
    on_saved: Callable[[object, Path], bool] | None = None,
) -> dict:
    """Run the source app's one-item-at-a-time capture flow.

    ``on_saved`` is called before “下一条” becomes available. It must persist the
    current screenshot/report/status and may raise to keep the user on the item.
    """
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ModuleNotFoundError as error:
        raise RuntimeError("交互截图模式需要 PySide6；无桌面环境时请改用 screenshots 批处理模式") from error

    _app(QtWidgets)
    staging = Path(output_dir) / ".staging" / "screenshots"
    staging.mkdir(parents=True, exist_ok=True)
    results: list[tuple[object, Path]] = []
    skipped: list[object] = []

    class Canvas(QtWidgets.QGraphicsView):
        def __init__(self, pixmap):
            super().__init__()
            self.scene = QtWidgets.QGraphicsScene(self)
            self.setScene(self.scene)
            self.scene.addPixmap(pixmap)
            self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
            self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            self.mode = "pen"
            self.start = None
            self.active = None
            self.history = []
            self.crop_rect = None

        def set_mode(self, mode):
            self.mode = mode
            self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag if mode == "pan" else QtWidgets.QGraphicsView.DragMode.NoDrag)

        def undo(self):
            if self.history:
                self.scene.removeItem(self.history.pop())

        def mousePressEvent(self, event):
            if event.button() != QtCore.Qt.MouseButton.LeftButton or self.mode == "pan":
                return super().mousePressEvent(event)
            point = self.mapToScene(event.position().toPoint())
            self.start = point
            if self.mode == "text":
                text, accepted = QtWidgets.QInputDialog.getText(self, "添加文字", "文字内容")
                if accepted and text:
                    item = self.scene.addText(text, QtGui.QFont("Arial", 15, QtGui.QFont.Weight.Bold))
                    item.setDefaultTextColor(QtGui.QColor("#ef4444"))
                    item.setPos(point)
                    self.history.append(item)
                self.start = None
            elif self.mode in {"pen", "highlighter"}:
                item = QtWidgets.QGraphicsPathItem(QtGui.QPainterPath(point))
                color, width = ((QtGui.QColor(255, 225, 50, 130), 18) if self.mode == "highlighter" else (QtGui.QColor("#ef4444"), 4))
                item.setPen(QtGui.QPen(color, width, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
                self.scene.addItem(item)
                self.active = item

        def mouseMoveEvent(self, event):
            if self.start is None:
                return super().mouseMoveEvent(event)
            point = self.mapToScene(event.position().toPoint())
            if self.mode in {"pen", "highlighter"}:
                path = self.active.path()
                path.lineTo(point)
                self.active.setPath(path)
            elif self.mode in {"rect", "ellipse", "crop"}:
                if self.active is not None:
                    self.scene.removeItem(self.active)
                rect = QtCore.QRectF(self.start, point).normalized()
                if self.mode == "rect":
                    self.active = self.scene.addRect(rect, QtGui.QPen(QtGui.QColor("#ef4444"), 4))
                elif self.mode == "ellipse":
                    self.active = self.scene.addEllipse(rect, QtGui.QPen(QtGui.QColor("#ef4444"), 4))
                else:
                    self.active = self.scene.addRect(rect, QtGui.QPen(QtGui.QColor("#2563eb"), 2, QtCore.Qt.PenStyle.DashLine))
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):
            if event.button() != QtCore.Qt.MouseButton.LeftButton or self.start is None:
                return super().mouseReleaseEvent(event)
            if self.mode == "crop" and self.active is not None:
                self.crop_rect = self.active.rect()
            if self.active is not None:
                self.history.append(self.active)
                self.active = None
            self.start = None
            super().mouseReleaseEvent(event)

        def export_image(self):
            source = self.crop_rect or self.scene.sceneRect()
            image = QtGui.QImage(int(source.width()), int(source.height()), QtGui.QImage.Format.Format_ARGB32)
            image.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(image)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            self.scene.render(painter, QtCore.QRectF(image.rect()), source)
            painter.end()
            return image

    class Annotator(QtWidgets.QDialog):
        def __init__(self, path):
            super().__init__()
            self.setWindowTitle("截图标注")
            self.resize(1100, 760)
            self.path = path
            self.canvas = Canvas(QtGui.QPixmap(str(path)))
            root = QtWidgets.QVBoxLayout(self)
            toolbar = QtWidgets.QHBoxLayout()
            for mode, title in (("pan", "拖动"), ("pen", "画笔"), ("highlighter", "荧光笔"), ("rect", "矩形"), ("ellipse", "椭圆"), ("text", "文字"), ("crop", "裁剪")):
                button = QtWidgets.QPushButton(title)
                button.clicked.connect(lambda _, value=mode: self.canvas.set_mode(value))
                toolbar.addWidget(button)
            undo = QtWidgets.QPushButton("撤销")
            undo.clicked.connect(self.canvas.undo)
            toolbar.addWidget(undo)
            toolbar.addStretch()
            cancel = QtWidgets.QPushButton("取消")
            cancel.clicked.connect(self.reject)
            toolbar.addWidget(cancel)
            save = QtWidgets.QPushButton("保存")
            save.setObjectName("primaryButton")
            save.clicked.connect(self.save)
            toolbar.addWidget(save)
            root.addLayout(toolbar)
            root.addWidget(self.canvas)

        def save(self):
            if not self.canvas.export_image().save(str(self.path)):
                QtWidgets.QMessageBox.critical(self, "保存失败", "标注图片保存失败")
                return
            self.accept()

    class Session(QtWidgets.QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("TestBox 测试证据执行")
            self.resize(620, 340)
            self.index = 0
            self.current_saved = False
            self.last_image = None
            root = QtWidgets.QVBoxLayout(self)
            self.title = QtWidgets.QLabel()
            self.title.setWordWrap(True)
            self.title.setStyleSheet("font-size:18px;font-weight:700")
            self.detail = QtWidgets.QLabel()
            self.detail.setWordWrap(True)
            self.detail.setMinimumHeight(110)
            root.addWidget(self.title)
            root.addWidget(self.detail)
            actions = QtWidgets.QHBoxLayout()
            self.capture = QtWidgets.QPushButton("截图并标注（F8）")
            self.capture.setObjectName("primaryButton")
            self.capture.clicked.connect(self.capture_item)
            self.next = QtWidgets.QPushButton("下一条")
            self.next.clicked.connect(self.next_item)
            self.skip = QtWidgets.QPushButton("跳过")
            self.skip.clicked.connect(self.skip_item)
            self.finish = QtWidgets.QPushButton("结束")
            self.finish.clicked.connect(self.reject)
            actions.addWidget(self.capture, stretch=2)
            actions.addWidget(self.next, stretch=1)
            actions.addWidget(self.skip)
            actions.addWidget(self.finish)
            root.addLayout(actions)
            tip = QtWidgets.QLabel("截图时 TestBox 主窗口和执行面板都会隐藏；保存后会先写入 Word 并回写 Excel，再进入下一条。")
            tip.setWordWrap(True)
            root.addWidget(tip)
            self.refresh()

        def refresh(self):
            if self.index >= len(items):
                self.accept()
                return
            item = items[self.index]
            self.current_saved = False
            self.last_image = None
            self.title.setText(f"第 {self.index + 1}/{len(items)} 项：{item.display_title}")
            detail = [f"验证点：{item.checkpoint}"]
            if item.step_note():
                detail.append(item.step_note())
            self.detail.setText("\n".join(detail))
            self.next.setEnabled(False)

        def keyPressEvent(self, event):
            if event.key() == QtCore.Qt.Key.Key_F8:
                self.capture_item()
                return
            super().keyPressEvent(event)

        def skip_item(self):
            if self.index < len(items):
                skipped.append(items[self.index])
                logger.info(f"跳过：第 {items[self.index].row_index} 行")
            self.index += 1
            self.refresh()

        def next_item(self):
            if not self.current_saved:
                QtWidgets.QMessageBox.information(self, "提示", "请先截图并保存标注。")
                return
            self.index += 1
            self.refresh()

        def capture_item(self):
            if self.index >= len(items):
                return
            item = items[self.index]
            self.capture.setEnabled(False)
            self.hide()
            QtWidgets.QApplication.processEvents()
            QtCore.QTimer.singleShot(450, lambda: self._capture_after_hide(item))

        def _restore(self):
            self.show()
            self.raise_()
            self.activateWindow()
            self.capture.setEnabled(True)

        def _capture_after_hide(self, item):
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is None:
                self._restore()
                QtWidgets.QMessageBox.warning(self, "截图失败", "未检测到主屏幕")
                return
            stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = staging / f"row-{item.row_index}-{stamp}.png"
            pixmap = screen.grabWindow(0)
            if pixmap.isNull() or not pixmap.save(str(path), "PNG"):
                self._restore()
                QtWidgets.QMessageBox.warning(self, "截图失败", "系统未返回有效截图，请检查屏幕录制权限")
                return
            annotator = Annotator(path)
            if annotator.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                logger.info(f"取消标注：第 {item.row_index} 行")
                self._restore()
                return
            try:
                if on_saved is not None and not on_saved(item, path):
                    raise RuntimeError("当前证据保存未完成")
            except Exception as error:
                logger.error(f"保存当前证据失败：{error}")
                self._restore()
                QtWidgets.QMessageBox.critical(self, "保存失败", str(error))
                return
            results.append((item, path))
            self.last_image = path
            self.current_saved = True
            logger.info(f"截图证据已保存：第 {item.row_index} 行")
            self._restore()
            self.next.setEnabled(True)

    session = Session()
    accepted = session.exec() == QtWidgets.QDialog.DialogCode.Accepted
    session.deleteLater()
    return {"saved": results, "skipped": skipped, "ended": not accepted and session.index < len(items)}
