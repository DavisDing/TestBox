"""Interactive screenshot session adapted from screenshot-to-word.

This module is intentionally isolated from the non-interactive plugin path: CLI and
headless executions continue to use evidence.build with pre-captured screenshots.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path


def run_session(items, output_dir: Path, logger) -> dict:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ModuleNotFoundError as error:
        raise RuntimeError("交互截图模式需要 PySide6；无桌面环境时请改用预先提供 screenshots 的批处理模式") from error

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    staging = output_dir / ".staging" / "screenshots"
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
                    item.setDefaultTextColor(QtGui.QColor("#ef4444")); item.setPos(point); self.history.append(item)
                self.start = None
            elif self.mode in {"pen", "highlighter"}:
                item = QtWidgets.QGraphicsPathItem(QtGui.QPainterPath(point))
                color, width = ((QtGui.QColor(255, 225, 50, 130), 18) if self.mode == "highlighter" else (QtGui.QColor("#ef4444"), 4))
                item.setPen(QtGui.QPen(color, width, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
                self.scene.addItem(item); self.active = item

        def mouseMoveEvent(self, event):
            if self.start is None:
                return super().mouseMoveEvent(event)
            point = self.mapToScene(event.position().toPoint())
            if self.mode in {"pen", "highlighter"}:
                path = self.active.path(); path.lineTo(point); self.active.setPath(path)
            elif self.mode in {"rect", "ellipse", "crop"}:
                if self.active is not None: self.scene.removeItem(self.active)
                rect = QtCore.QRectF(self.start, point).normalized()
                if self.mode == "rect": self.active = self.scene.addRect(rect, QtGui.QPen(QtGui.QColor("#ef4444"), 4))
                elif self.mode == "ellipse": self.active = self.scene.addEllipse(rect, QtGui.QPen(QtGui.QColor("#ef4444"), 4))
                else: self.active = self.scene.addRect(rect, QtGui.QPen(QtGui.QColor("#2563eb"), 2, QtCore.Qt.PenStyle.DashLine))
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event):
            if event.button() != QtCore.Qt.MouseButton.LeftButton or self.start is None:
                return super().mouseReleaseEvent(event)
            if self.mode == "crop" and self.active is not None: self.crop_rect = self.active.rect()
            if self.active is not None: self.history.append(self.active); self.active = None
            self.start = None
            super().mouseReleaseEvent(event)

        def export_image(self):
            source = self.crop_rect or self.scene.sceneRect()
            image = QtGui.QImage(int(source.width()), int(source.height()), QtGui.QImage.Format.Format_ARGB32)
            image.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(image); painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            self.scene.render(painter, QtCore.QRectF(image.rect()), source); painter.end()
            return image

    class Annotator(QtWidgets.QDialog):
        def __init__(self, path):
            super().__init__(); self.setWindowTitle("截图标注"); self.resize(1100, 760); self.path = path
            self.canvas = Canvas(QtGui.QPixmap(str(path)))
            root = QtWidgets.QVBoxLayout(self); toolbar = QtWidgets.QHBoxLayout()
            for mode, title in (("pan", "拖动"), ("pen", "画笔"), ("highlighter", "荧光笔"), ("rect", "矩形"), ("ellipse", "椭圆"), ("text", "文字"), ("crop", "裁剪")):
                button = QtWidgets.QPushButton(title); button.clicked.connect(lambda _, value=mode: self.canvas.set_mode(value)); toolbar.addWidget(button)
            undo = QtWidgets.QPushButton("撤销"); undo.clicked.connect(self.canvas.undo); toolbar.addWidget(undo); toolbar.addStretch()
            cancel = QtWidgets.QPushButton("取消"); cancel.clicked.connect(self.reject); toolbar.addWidget(cancel)
            save = QtWidgets.QPushButton("保存并继续"); save.setObjectName("primaryButton"); save.clicked.connect(self.save); toolbar.addWidget(save)
            root.addLayout(toolbar); root.addWidget(self.canvas)

        def save(self):
            if not self.canvas.export_image().save(str(self.path)):
                QtWidgets.QMessageBox.critical(self, "保存失败", "标注图片保存失败")
                return
            self.accept()

    class Session(QtWidgets.QDialog):
        def __init__(self):
            super().__init__(); self.setWindowTitle("TestBox 测试证据执行"); self.resize(620, 300); self.index = 0
            root = QtWidgets.QVBoxLayout(self); self.title = QtWidgets.QLabel(); self.title.setWordWrap(True); self.title.setStyleSheet("font-size:18px;font-weight:700")
            self.detail = QtWidgets.QLabel(); self.detail.setWordWrap(True); self.detail.setMinimumHeight(90); root.addWidget(self.title); root.addWidget(self.detail)
            actions = QtWidgets.QHBoxLayout(); self.capture = QtWidgets.QPushButton("截图并标注（F8）"); self.capture.setObjectName("primaryButton"); self.capture.clicked.connect(self.capture_item)
            self.skip = QtWidgets.QPushButton("跳过"); self.skip.clicked.connect(self.skip_item); self.finish = QtWidgets.QPushButton("结束"); self.finish.clicked.connect(self.reject)
            actions.addWidget(self.capture); actions.addWidget(self.skip); actions.addStretch(); actions.addWidget(self.finish); root.addLayout(actions); self.refresh()

        def refresh(self):
            if self.index >= len(items): self.accept(); return
            item = items[self.index]; self.title.setText(f"第 {self.index + 1}/{len(items)} 项：{item.case_name}")
            detail = [f"验证点：{item.checkpoint}"]
            if item.step_note(): detail.append(item.step_note())
            self.detail.setText("\n".join(detail))

        def keyPressEvent(self, event):
            if event.key() == QtCore.Qt.Key.Key_F8: self.capture_item(); return
            super().keyPressEvent(event)

        def skip_item(self):
            if self.index < len(items):
                skipped.append(items[self.index])
            self.index += 1; self.refresh()

        def capture_item(self):
            item = items[self.index]
            self.hide(); QtWidgets.QApplication.processEvents(); QtCore.QTimer.singleShot(180, lambda: self._capture_after_hide(item))

        def _capture_after_hide(self, item):
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is None:
                self.show(); QtWidgets.QMessageBox.warning(self, "截图失败", "未检测到主屏幕"); return
            stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = staging / f"row-{item.row_index}-{stamp}.png"
            pixmap = screen.grabWindow(0)
            if pixmap.isNull() or not pixmap.save(str(path), "PNG"):
                self.show(); QtWidgets.QMessageBox.warning(self, "截图失败", "系统未返回有效截图，请检查屏幕录制权限"); return
            self.show(); annotator = Annotator(path)
            if annotator.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                results.append((item, path)); logger.info(f"截图证据已保存：第 {item.row_index} 行")
                self.index += 1; self.refresh()

    session = Session()
    accepted = session.exec() == QtWidgets.QDialog.DialogCode.Accepted
    session.deleteLater()
    return {"saved": results, "skipped": skipped, "ended": not accepted and session.index < len(items)}
