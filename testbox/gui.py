from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from testbox.core.runtime import Runtime


# The frozen GUI executable is also the plugin Host for GUI-initiated tasks.
# Handle that internal mode before importing or starting Qt so it works in
# headless child processes as well as in the desktop application.
if __name__ == "__main__" and len(sys.argv) == 2 and sys.argv[1] == "--plugin-host":
    from testbox.core.host import main as host_main
    host_main()
    raise SystemExit


def _qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ModuleNotFoundError as error:
        raise SystemExit("TestBox GUI 需要桌面依赖，请执行: pip install -e '.[desktop]'") from error
    return QtCore, QtGui, QtWidgets


QtCore, QtGui, QtWidgets = _qt()


# ==============================================================================
# 异步执行 Worker (基于 Runtime)
# ==============================================================================

class WorkerSignals(QtCore.QObject):
    finished = QtCore.Signal(str, object, float)  # task_id, result, elapsed_seconds
    failed = QtCore.Signal(str, str, float)       # command, error_message, elapsed_seconds


class RuntimeWorker(QtCore.QRunnable):
    def __init__(self, root: Path | None, command: str, params: dict[str, Any]):
        super().__init__()
        self.root = root
        self.command = command
        self.params = params
        self.signals = WorkerSignals()

    @QtCore.Slot()
    def run(self):
        start_time = time.perf_counter()
        try:
            runtime = Runtime(self.root)
            try:
                task_id, result = runtime.run(self.command, self.params)
                elapsed = time.perf_counter() - start_time
                self.signals.finished.emit(task_id, result, elapsed)
            finally:
                runtime.close()
        except Exception as error:
            elapsed = time.perf_counter() - start_time
            self.signals.failed.emit(self.command, str(error), elapsed)


# ==============================================================================
# 截图标注画板与对话框 (用于截图/证据等辅助交互)
# ==============================================================================

class AnnotationCanvas(QtWidgets.QGraphicsView):
    def __init__(self, pixmap: QtGui.QPixmap):
        super().__init__()
        self.scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self.scene)
        self.scene.addPixmap(pixmap)
        self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        self.mode = "pen"
        self.start = None
        self.active = None
        self.history: list[QtWidgets.QGraphicsItem] = []
        self.crop_rect = None

    def undo(self):
        if self.history:
            item = self.history.pop()
            self.scene.removeItem(item)

    def set_mode(self, mode: str):
        self.mode = mode
        self.setDragMode(
            QtWidgets.QGraphicsView.DragMode.ScrollHandDrag
            if mode == "pan"
            else QtWidgets.QGraphicsView.DragMode.NoDrag
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() != QtCore.Qt.MouseButton.LeftButton or self.mode == "pan":
            return super().mousePressEvent(event)
        point = self.mapToScene(event.position().toPoint())
        self.start = point
        if self.mode == "text":
            value, accepted = QtWidgets.QInputDialog.getText(self, "添加文字", "文字内容")
            if accepted and value:
                item = self.scene.addText(value, QtGui.QFont("Arial", 15, QtGui.QFont.Weight.Bold))
                item.setDefaultTextColor(QtGui.QColor("#ef4444"))
                item.setPos(point)
                self.history.append(item)
            self.start = None
        elif self.mode in {"pen", "highlighter"}:
            path = QtGui.QPainterPath(point)
            item = QtWidgets.QGraphicsPathItem(path)
            color, width = (
                (QtGui.QColor(255, 225, 50, 130), 18)
                if self.mode == "highlighter"
                else (QtGui.QColor("#ef4444"), 4)
            )
            item.setPen(QtGui.QPen(color, width, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap))
            self.scene.addItem(item)
            self.active = item

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        if self.start is None:
            return super().mouseMoveEvent(event)
        current = self.mapToScene(event.position().toPoint())
        if self.mode in {"pen", "highlighter"}:
            path = self.active.path()
            path.lineTo(current)
            self.active.setPath(path)
        elif self.mode in {"rect", "ellipse", "crop"}:
            if self.active is not None:
                self.scene.removeItem(self.active)
            rectangle = QtCore.QRectF(self.start, current).normalized()
            pen = QtGui.QPen(QtGui.QColor("#ef4444"), 4)
            if self.mode == "rect":
                self.active = self.scene.addRect(rectangle, pen)
            elif self.mode == "ellipse":
                self.active = self.scene.addEllipse(rectangle, pen)
            elif self.mode == "crop":
                crop_pen = QtGui.QPen(QtGui.QColor("#2563eb"), 2, QtCore.Qt.PenStyle.DashLine)
                self.active = self.scene.addRect(rectangle, crop_pen)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        if event.button() != QtCore.Qt.MouseButton.LeftButton or self.start is None:
            return super().mouseReleaseEvent(event)
        if self.mode == "crop" and self.active is not None:
            self.crop_rect = self.active.rect()
        if self.active is not None:
            self.history.append(self.active)
            self.active = None
        self.start = None
        super().mouseReleaseEvent(event)

    def export_image(self) -> QtGui.QImage:
        source_rect = self.crop_rect or self.scene.sceneRect()
        image = QtGui.QImage(
            int(source_rect.width()),
            int(source_rect.height()),
            QtGui.QImage.Format.Format_ARGB32,
        )
        image.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.scene.render(painter, QtCore.QRectF(image.rect()), source_rect)
        painter.end()
        return image


class AnnotationDialog(QtWidgets.QDialog):
    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("截图标注工具 - TestBox")
        self.resize(960, 680)
        self.image_path = image_path
        self.canvas = AnnotationCanvas(QtGui.QPixmap(image_path))

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(8)

        self.btn_group = QtWidgets.QButtonGroup(self)
        tools = [
            ("pan", "✋ 拖动"),
            ("pen", "✏️ 画笔"),
            ("highlighter", "🖍️ 荧光笔"),
            ("rect", " 矩形"),
            ("ellipse", " 椭圆"),
            ("text", "🔤 文字"),
            ("crop", "✂️ 裁剪"),
        ]
        for mode, title in tools:
            btn = QtWidgets.QPushButton(title)
            btn.setCheckable(True)
            if mode == "pen":
                btn.setChecked(True)
            btn.clicked.connect(lambda _, m=mode: self.canvas.set_mode(m))
            self.btn_group.addButton(btn)
            toolbar.addWidget(btn)

        toolbar.addSpacing(12)
        undo_btn = QtWidgets.QPushButton("↶ 撤销")
        undo_btn.clicked.connect(self.canvas.undo)
        toolbar.addWidget(undo_btn)

        toolbar.addStretch()

        save_btn = QtWidgets.QPushButton("保存标注并更新")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self.save_and_close)
        toolbar.addWidget(save_btn)

        layout.addLayout(toolbar)
        layout.addWidget(self.canvas)

    def save_and_close(self):
        annotated_image = self.canvas.export_image()
        annotated_image.save(self.image_path)
        self.accept()


# ==============================================================================
# 通用 UI 组件：文件选择器 / 集合选择器
# ==============================================================================

class SingleFilePicker(QtWidgets.QWidget):
    """单个文件选择组件"""
    valueChanged = QtCore.Signal(str)

    def __init__(self, placeholder: str = "点击选择或拖入文件...", filter_str: str = "All Files (*.*)", parent=None):
        super().__init__(parent)
        self.filter_str = filter_str
        self._path = ""

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.line_edit = QtWidgets.QLineEdit()
        self.line_edit.setPlaceholderText(placeholder)
        self.line_edit.setReadOnly(True)

        self.btn_browse = QtWidgets.QPushButton("选择文件...")
        self.btn_browse.setObjectName("secondaryButton")
        self.btn_browse.clicked.connect(self._choose_file)

        self.btn_clear = QtWidgets.QPushButton("清除")
        self.btn_clear.setObjectName("smallButton")
        self.btn_clear.clicked.connect(self.clear)
        self.btn_clear.setVisible(False)

        self.file_info_lbl = QtWidgets.QLabel("")
        self.file_info_lbl.setObjectName("mutedText")

        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self.btn_browse)
        layout.addWidget(self.btn_clear)

    def _choose_file(self):
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择文件", "", self.filter_str)
        if file_path:
            self.set_path(file_path)

    def set_path(self, path: str):
        self._path = path
        self.line_edit.setText(path)
        p = Path(path)
        if p.exists():
            size_kb = p.stat().st_size / 1024.0
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{(size_kb/1024):.2f} MB"
            self.line_edit.setToolTip(f"完整路径: {path}\n大小: {size_str}")
            self.btn_clear.setVisible(True)
        else:
            self.line_edit.setToolTip(path)
            self.btn_clear.setVisible(bool(path))
        self.valueChanged.emit(self._path)

    def get_path(self) -> str:
        return self._path

    def clear(self):
        self.set_path("")


class MultiFilesPicker(QtWidgets.QWidget):
    """多文件列表选择组件"""
    valueChanged = QtCore.Signal(list)

    def __init__(self, filter_str: str = "All Files (*.*)", parent=None):
        super().__init__(parent)
        self.filter_str = filter_str
        self._paths: list[str] = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        btn_bar = QtWidgets.QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_add = QtWidgets.QPushButton("➕ 添加文件...")
        self.btn_add.setObjectName("secondaryButton")
        self.btn_add.clicked.connect(self._add_files)

        self.btn_remove = QtWidgets.QPushButton("➖ 移除选中")
        self.btn_remove.setObjectName("smallButton")
        self.btn_remove.clicked.connect(self._remove_selected)

        self.btn_clear = QtWidgets.QPushButton("全部清空")
        self.btn_clear.setObjectName("smallButton")
        self.btn_clear.clicked.connect(self.clear)

        self.count_label = QtWidgets.QLabel("已选 0 个文件")
        self.count_label.setObjectName("mutedText")

        btn_bar.addWidget(self.btn_add)
        btn_bar.addWidget(self.btn_remove)
        btn_bar.addWidget(self.btn_clear)
        btn_bar.addStretch()
        btn_bar.addWidget(self.count_label)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setFixedHeight(120)

        layout.addLayout(btn_bar)
        layout.addWidget(self.list_widget)

    def _add_files(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "选择多个文件", "", self.filter_str)
        if files:
            for f in files:
                if f not in self._paths:
                    self._paths.append(f)
            self._sync_list()

    def _remove_selected(self):
        selected_items = self.list_widget.selectedItems()
        if not selected_items:
            return
        selected_texts = {item.data(QtCore.Qt.ItemDataRole.UserRole) for item in selected_items}
        self._paths = [p for p in self._paths if p not in selected_texts]
        self._sync_list()

    def clear(self):
        self._paths = []
        self._sync_list()

    def set_paths(self, paths: list[str]):
        self._paths = list(paths)
        self._sync_list()

    def get_paths(self) -> list[str]:
        return list(self._paths)

    def _sync_list(self):
        self.list_widget.clear()
        for path_str in self._paths:
            p = Path(path_str)
            item = QtWidgets.QListWidgetItem()
            if p.exists():
                size_kb = p.stat().st_size / 1024.0
                size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{(size_kb/1024):.2f} MB"
                item.setText(f"📄 {p.name} ({size_str})")
            else:
                item.setText(f"📄 {p.name} (文件不存在)")
            item.setToolTip(path_str)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, path_str)
            self.list_widget.addItem(item)
        self.count_label.setText(f"已选 {len(self._paths)} 个文件")
        self.valueChanged.emit(self._paths)


# ==============================================================================
# Schema 动态参数表单驱动组件 (Schema-driven Form)
# ==============================================================================

class DynamicSchemaForm(QtWidgets.QWidget):
    """
    根据 get_command_schema(command) 返回的 JSON Schema 动态生成参数输入表单。
    支持: string, integer, number, boolean, enum, array, object, file-path, 默认值, 范围校验等。
    支持基础参数与高级参数自动折叠分类。
    """
    def __init__(self, schema: dict[str, Any], command_name: str = "", parent=None):
        super().__init__(parent)
        self.schema = schema or {}
        self.command_name = command_name
        self.fields: dict[str, Any] = {}
        self.error_labels: dict[str, QtWidgets.QLabel] = {}
        self._init_ui()

    def _init_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(16)

        properties: dict[str, dict] = self.schema.get("properties", {})
        required_keys: list[str] = self.schema.get("required", [])

        # 分类为基础参数和高级参数
        basic_props = {}
        advanced_props = {}

        # 启发式归类：如果是必填项、或常用核心参数（count, format, input, template）归为基础参数；其余归为高级
        for key, spec in properties.items():
            if key in required_keys or key in ("count", "format", "input", "dialect", "template", "seed", "rules"):
                basic_props[key] = spec
            else:
                advanced_props[key] = spec

        # 1. 必填与主要参数组
        if basic_props:
            basic_box = QtWidgets.QGroupBox("主要参数设置 (Basic Parameters)")
            basic_layout = QtWidgets.QVBoxLayout(basic_box)
            basic_layout.setSpacing(12)
            basic_layout.setContentsMargins(14, 16, 14, 14)

            for key, spec in basic_props.items():
                w = self._create_field_widget(key, spec, is_required=(key in required_keys))
                basic_layout.addWidget(w)
            main_layout.addWidget(basic_box)

        # 2. 高级参数折叠面板
        if advanced_props:
            adv_container = QtWidgets.QWidget()
            adv_container.setObjectName("cardPanel")
            adv_layout = QtWidgets.QVBoxLayout(adv_container)
            adv_layout.setContentsMargins(14, 12, 14, 14)
            adv_layout.setSpacing(10)

            # 折叠切换按钮
            self.adv_toggle_btn = QtWidgets.QPushButton("▶ 展开高级参数配置 (Advanced Options)")
            self.adv_toggle_btn.setObjectName("secondaryButton")
            self.adv_toggle_btn.setCheckable(True)
            self.adv_toggle_btn.setChecked(False)

            self.adv_content_widget = QtWidgets.QWidget()
            adv_fields_layout = QtWidgets.QVBoxLayout(self.adv_content_widget)
            adv_fields_layout.setContentsMargins(0, 8, 0, 0)
            adv_fields_layout.setSpacing(12)

            for key, spec in advanced_props.items():
                w = self._create_field_widget(key, spec, is_required=(key in required_keys))
                adv_fields_layout.addWidget(w)

            self.adv_content_widget.setVisible(False)
            self.adv_toggle_btn.toggled.connect(self._on_toggle_advanced)

            adv_layout.addWidget(self.adv_toggle_btn)
            adv_layout.addWidget(self.adv_content_widget)
            main_layout.addWidget(adv_container)

        main_layout.addStretch()

    def _on_toggle_advanced(self, checked: bool):
        self.adv_content_widget.setVisible(checked)
        if checked:
            self.adv_toggle_btn.setText("▼ 收起高级参数配置 (Advanced Options)")
        else:
            self.adv_toggle_btn.setText("▶ 展开高级参数配置 (Advanced Options)")

    def _create_field_widget(self, key: str, spec: dict[str, Any], is_required: bool) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 头部标签栏
        label_bar = QtWidgets.QHBoxLayout()
        label_bar.setSpacing(6)

        title_text = f"<b>{key}</b>"
        if is_required:
            title_text += " <span style='color: #ef4444; font-weight: bold;'>*必填</span>"
        lbl = QtWidgets.QLabel(title_text)
        label_bar.addWidget(lbl)

        # 类型或格式标记
        type_str = spec.get("type", "string")
        fmt = spec.get("format")
        badge_text = f"{type_str}" + (f":{fmt}" if fmt else "")
        badge = QtWidgets.QLabel(f"[{badge_text}]")
        badge.setObjectName("tagLabelMuted")
        label_bar.addWidget(badge)

        desc = spec.get("description")
        if desc:
            desc_lbl = QtWidgets.QLabel(f"- {desc}")
            desc_lbl.setObjectName("mutedText")
            label_bar.addWidget(desc_lbl)

        label_bar.addStretch()
        layout.addLayout(label_bar)

        # 表单输入控件
        ctrl = None
        enum_values = spec.get("enum")
        default_val = spec.get("default")

        if fmt == "file-path":
            filter_str = "All Files (*.*)"
            if "sql" in key or "sql" in self.command_name:
                filter_str = "SQL Files (*.sql *.ddl);;All Files (*.*)"
            elif "excel" in key or "input" in key:
                filter_str = "Excel / SQL / Text (*.xlsx *.xlsm *.sql *.csv *.json);;All Files (*.*)"
            ctrl = SingleFilePicker(placeholder=f"请选择 {key} 文件...", filter_str=filter_str)
            if default_val:
                ctrl.set_path(str(default_val))
            self.fields[key] = ("file-path", ctrl)
            layout.addWidget(ctrl)

        elif enum_values:
            combo = QtWidgets.QComboBox()
            # 如果不是必填且无默认值，添加一个空项
            if not is_required and default_val is None:
                combo.addItem("(未指定 / 默认)", None)
            for item in enum_values:
                combo.addItem(str(item), item)
            if default_val is not None and default_val in enum_values:
                combo.setCurrentText(str(default_val))
            self.fields[key] = ("enum", combo)
            layout.addWidget(combo)

        elif type_str == "boolean":
            chk = QtWidgets.QCheckBox("启用 / 设为 True")
            if default_val is not None:
                chk.setChecked(bool(default_val))
            else:
                chk.setChecked(False)
            self.fields[key] = ("boolean", chk)
            layout.addWidget(chk)

        elif type_str in ("integer", "number"):
            spin = QtWidgets.QSpinBox() if type_str == "integer" else QtWidgets.QDoubleSpinBox()
            min_v = spec.get("minimum", -999999999)
            max_v = spec.get("maximum", 999999999)
            spin.setRange(int(min_v), int(max_v))
            if default_val is not None:
                spin.setValue(default_val)
            elif is_required and min_v > 0:
                spin.setValue(int(min_v))
            elif key == "count":
                spin.setValue(10)
            else:
                spin.setValue(0)
            self.fields[key] = ("number", spin)
            layout.addWidget(spin)

        elif type_str == "array":
            items_spec = spec.get("items", {})
            if items_spec.get("format") == "file-path" or key in ("screenshots", "existing_reports"):
                ctrl = MultiFilesPicker(filter_str="Image / Report Files (*.png *.jpg *.jpeg *.docx *.xlsx);;All Files (*.*)")
                self.fields[key] = ("array-files", ctrl)
                layout.addWidget(ctrl)
            else:
                edit = QtWidgets.QLineEdit()
                edit.setPlaceholderText("请输入逗号分隔的值或 JSON 数组，如: a, b, c 或 [1, 2]")
                if default_val:
                    edit.setText(json.dumps(default_val, ensure_ascii=False) if isinstance(default_val, (list, dict)) else str(default_val))
                self.fields[key] = ("array-text", edit)
                layout.addWidget(edit)

        elif type_str == "object":
            edit = QtWidgets.QLineEdit()
            edit.setPlaceholderText('请输入 JSON 对象，例如: {"case_id": "用例编号", "title": "用例名称"}')
            if default_val:
                edit.setText(json.dumps(default_val, ensure_ascii=False))
            self.fields[key] = ("object-text", edit)
            layout.addWidget(edit)

        else:
            # 默认 string
            edit = QtWidgets.QLineEdit()
            if default_val is not None:
                edit.setText(str(default_val))
            edit.setPlaceholderText(f"请输入 {key}...")
            self.fields[key] = ("string", edit)
            layout.addWidget(edit)

        # 错误提示标签（精准显示在字段下方）
        err_lbl = QtWidgets.QLabel("")
        err_lbl.setObjectName("fieldErrorLabel")
        err_lbl.setVisible(False)
        self.error_labels[key] = err_lbl
        layout.addWidget(err_lbl)

        return container

    def get_values(self) -> dict[str, Any]:
        """解析并返回当前表单填充的全部参数字典"""
        params = {}
        for key, (ftype, widget) in self.fields.items():
            if ftype == "file-path":
                val = widget.get_path().strip()
                if val:
                    params[key] = val
            elif ftype == "enum":
                val = widget.currentData()
                if val is not None:
                    params[key] = val
            elif ftype == "boolean":
                params[key] = widget.isChecked()
            elif ftype == "number":
                params[key] = widget.value()
            elif ftype == "array-files":
                vals = widget.get_paths()
                if vals:
                    params[key] = vals
            elif ftype == "array-text":
                txt = widget.text().strip()
                if txt:
                    if txt.startswith("[") and txt.endswith("]"):
                        try:
                            params[key] = json.loads(txt)
                        except Exception:
                            params[key] = [x.strip() for x in txt.strip("[]").split(",") if x.strip()]
                    else:
                        params[key] = [x.strip() for x in txt.split(",") if x.strip()]
            elif ftype == "object-text":
                txt = widget.text().strip()
                if txt:
                    try:
                        params[key] = json.loads(txt)
                    except Exception:
                        params[key] = txt
            elif ftype == "string":
                val = widget.text().strip()
                if val:
                    params[key] = val
        return params

    def set_values(self, values: dict[str, Any]):
        """根据传入字典回填表单"""
        for key, val in values.items():
            if key not in self.fields:
                continue
            ftype, widget = self.fields[key]
            if ftype == "file-path":
                widget.set_path(str(val))
            elif ftype == "enum":
                idx = widget.findData(val)
                if idx >= 0:
                    widget.setCurrentIndex(idx)
                else:
                    widget.setCurrentText(str(val))
            elif ftype == "boolean":
                widget.setChecked(bool(val))
            elif ftype == "number":
                widget.setValue(val)
            elif ftype == "array-files" and isinstance(val, list):
                widget.set_paths(val)
            elif ftype == "array-text":
                if isinstance(val, (list, tuple)):
                    widget.setText(", ".join(str(x) for x in val))
                else:
                    widget.setText(str(val))
            elif ftype == "object-text":
                if isinstance(val, dict):
                    widget.setText(json.dumps(val, ensure_ascii=False))
                else:
                    widget.setText(str(val))
            elif ftype == "string":
                widget.setText(str(val))

    def clear_errors(self):
        for lbl in self.error_labels.values():
            lbl.setText("")
            lbl.setVisible(False)

    def set_field_error(self, key: str, message: str):
        if key in self.error_labels:
            lbl = self.error_labels[key]
            lbl.setText(f"❌ {message}")
            lbl.setVisible(True)

    def validate_locally(self) -> tuple[bool, str]:
        """进行基本必填项和本地 Schema 检查"""
        self.clear_errors()
        valid = True
        first_err = ""
        required_keys: list[str] = self.schema.get("required", [])

        values = self.get_values()
        for req in required_keys:
            if req not in values or values[req] is None or values[req] == "" or values[req] == []:
                self.set_field_error(req, f"此项为必填字段，请输入或选择有效值")
                if not first_err:
                    first_err = f"必填字段 [{req}] 不能为空"
                valid = False

        return valid, first_err


# ==============================================================================
# 视图 1：工具目录 (Tools Catalog)
# ==============================================================================

class ToolCardWidget(QtWidgets.QFrame):
    """工具目录中的命令卡片"""
    clicked = QtCore.Signal(str)  # command_name

    def __init__(self, command_name: str, cmd_desc: str, manifest: Any, parent=None):
        super().__init__(parent)
        self.command_name = command_name
        self.setObjectName("toolCard")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 头部：命令名称与插件标签
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setSpacing(8)

        name_lbl = QtWidgets.QLabel(command_name)
        name_lbl.setObjectName("toolCardTitle")

        plugin_tag = QtWidgets.QLabel(f"{manifest.name} v{manifest.version}")
        plugin_tag.setObjectName("tagLabel")

        top_bar.addWidget(name_lbl)
        top_bar.addWidget(plugin_tag)
        top_bar.addStretch()

        # 分类与状态徽标
        cat_tag = QtWidgets.QLabel(manifest.category.upper())
        cat_tag.setObjectName("tagLabelMuted")
        top_bar.addWidget(cat_tag)

        layout.addLayout(top_bar)

        # 命令描述
        desc_lbl = QtWidgets.QLabel(cmd_desc or manifest.description or "暂无详细描述")
        desc_lbl.setObjectName("toolCardDesc")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        # 底部特性标签
        caps = manifest.capabilities or {}
        caps_bar = QtWidgets.QHBoxLayout()
        caps_bar.setSpacing(8)

        conc = caps.get("concurrency", True)
        conc_lbl = QtWidgets.QLabel("⚡ 支持并发" if conc else "🔒 串行执行")
        conc_lbl.setObjectName("tagLabelMuted")
        caps_bar.addWidget(conc_lbl)

        fs = caps.get("filesystem", "none")
        fs_lbl = QtWidgets.QLabel(f"📁 文件: {fs}")
        fs_lbl.setObjectName("tagLabelMuted")
        caps_bar.addWidget(fs_lbl)

        caps_bar.addStretch()

        action_lbl = QtWidgets.QLabel("进入命令配置 →")
        action_lbl.setStyleSheet("color: #2563eb; font-weight: 600; font-size: 12px;")
        caps_bar.addWidget(action_lbl)

        layout.addLayout(caps_bar)

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit(self.command_name)
        super().mousePressEvent(event)


class ToolCatalogView(QtWidgets.QWidget):
    """工具目录页面：支持分类筛选、关键字搜索、直观卡片网格"""
    commandSelected = QtCore.Signal(str)

    def __init__(self, runtime: Runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.all_cards: list[tuple[str, str, str, ToolCardWidget]] = []
        self._init_ui()

    def _init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # 页面标题
        header_box = QtWidgets.QHBoxLayout()
        title_box = QtWidgets.QVBoxLayout()
        title_lbl = QtWidgets.QLabel("工具目录 (Tools Catalog)")
        title_lbl.setObjectName("pageTitle")
        subtitle_lbl = QtWidgets.QLabel("浏览并运行 TestBox 已发现的测试效能工具插件与命令")
        subtitle_lbl.setObjectName("mutedText")
        title_box.addWidget(title_lbl)
        title_box.addWidget(subtitle_lbl)

        header_box.addLayout(title_box)
        header_box.addStretch()

        refresh_btn = QtWidgets.QPushButton("🔄 刷新插件目录")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.clicked.connect(self.reload_tools)
        header_box.addWidget(refresh_btn)

        layout.addLayout(header_box)

        # 搜索与分类过滤条
        filter_bar = QtWidgets.QHBoxLayout()
        filter_bar.setSpacing(12)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索命令、插件名称或描述关键字...")
        self.search_input.textChanged.connect(self._filter_cards)
        filter_bar.addWidget(self.search_input, 2)

        self.category_combo = QtWidgets.QComboBox()
        self.category_combo.addItem("全部分类 (All Categories)", "all")
        self.category_combo.currentIndexChanged.connect(self._filter_cards)
        filter_bar.addWidget(self.category_combo, 1)

        layout.addLayout(filter_bar)

        # 滚动区域放置卡片
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        self.cards_container = QtWidgets.QWidget()
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)

        # 空状态提示组件
        self.empty_widget = QtWidgets.QWidget()
        empty_layout = QtWidgets.QVBoxLayout(self.empty_widget)
        empty_layout.setContentsMargins(40, 60, 40, 60)
        empty_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        empty_icon = QtWidgets.QLabel("📦")
        empty_icon.setStyleSheet("font-size: 48px;")
        empty_icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        empty_text = QtWidgets.QLabel("未找到匹配的工具插件或命令")
        empty_text.setStyleSheet("color: #64748b; font-size: 15px; font-weight: 500;")
        empty_text.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon)
        empty_layout.addWidget(empty_text)
        self.empty_widget.setVisible(False)
        self.cards_layout.addWidget(self.empty_widget)

        scroll.setWidget(self.cards_container)
        layout.addWidget(scroll, 1)

        self.reload_tools()

    def reload_tools(self):
        # 清除旧卡片
        for _, _, _, card in self.all_cards:
            self.cards_layout.removeWidget(card)
            card.deleteLater()
        self.all_cards.clear()

        commands_map = self.runtime.list_commands()
        categories = set()

        for cmd_name, manifest in sorted(commands_map.items()):
            # 获取命令描述
            cmd_desc = ""
            for cmd_obj in manifest.commands:
                if cmd_obj.name == cmd_name:
                    cmd_desc = cmd_obj.description
                    break

            card = ToolCardWidget(cmd_name, cmd_desc, manifest)
            card.clicked.connect(self.commandSelected.emit)
            self.cards_layout.addWidget(card)
            self.all_cards.append((cmd_name, manifest.name, manifest.category, card))
            categories.add(manifest.category)

        # 刷新分类下拉
        current_cat = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("全部分类 (All Categories)", "all")
        for cat in sorted(categories):
            self.category_combo.addItem(f"分类: {cat.upper()}", cat)
        if current_cat:
            idx = self.category_combo.findData(current_cat)
            if idx >= 0:
                self.category_combo.setCurrentIndex(idx)
        self.category_combo.blockSignals(False)

        self.cards_layout.addStretch()
        self._filter_cards()

    def _filter_cards(self):
        query = self.search_input.text().strip().lower()
        selected_cat = self.category_combo.currentData()

        visible_count = 0
        for cmd_name, plugin_name, category, card in self.all_cards:
            match_query = (not query) or (query in cmd_name.lower()) or (query in plugin_name.lower())
            match_cat = (selected_cat == "all") or (category == selected_cat)
            show = match_query and match_cat
            card.setVisible(show)
            if show:
                visible_count += 1

        self.empty_widget.setVisible(visible_count == 0)


# ==============================================================================
# 视图 2：命令配置与参数表单 (Command Form & Execute)
# ==============================================================================

class CommandDetailFormView(QtWidgets.QWidget):
    """
    两栏式命令详情与执行配置页面：
    左栏：Schema 驱动的参数表单 + 实时校验
    右栏：命令与插件元数据、输入输出规范说明、快速预设/重置操作
    """
    executeRequested = QtCore.Signal(str, dict)  # command_name, params
    backToCatalog = QtCore.Signal()

    def __init__(self, runtime: Runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.current_command: str = ""
        self.current_manifest: Any = None
        self.current_schema: dict = {}
        self.form_widget: DynamicSchemaForm | None = None
        self._init_ui()

    def _init_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(24, 16, 24, 20)
        main_layout.setSpacing(14)

        # 顶部返回与导航栏
        nav_bar = QtWidgets.QHBoxLayout()
        self.back_btn = QtWidgets.QPushButton("← 返回工具目录")
        self.back_btn.setObjectName("secondaryButton")
        self.back_btn.clicked.connect(self.backToCatalog.emit)
        nav_bar.addWidget(self.back_btn)

        self.cmd_title_lbl = QtWidgets.QLabel("")
        self.cmd_title_lbl.setObjectName("pageTitle")
        nav_bar.addWidget(self.cmd_title_lbl)

        self.plugin_badge = QtWidgets.QLabel("")
        self.plugin_badge.setObjectName("tagLabel")
        nav_bar.addWidget(self.plugin_badge)

        nav_bar.addStretch()
        main_layout.addLayout(nav_bar)

        # 主体左右分栏
        split_layout = QtWidgets.QHBoxLayout()
        split_layout.setSpacing(18)

        # === 左栏：Schema 表单滚动区 ===
        form_panel = QtWidgets.QWidget()
        form_layout = QtWidgets.QVBoxLayout(form_panel)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)

        self.form_scroll = QtWidgets.QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        self.form_container = QtWidgets.QWidget()
        self.form_container_layout = QtWidgets.QVBoxLayout(self.form_container)
        self.form_container_layout.setContentsMargins(0, 0, 0, 0)
        self.form_scroll.setWidget(self.form_container)

        form_layout.addWidget(self.form_scroll, 1)

        # 底部执行与操作栏
        action_bar = QtWidgets.QHBoxLayout()
        action_bar.setSpacing(12)

        self.reset_btn = QtWidgets.QPushButton("重置表单")
        self.reset_btn.setObjectName("secondaryButton")
        self.reset_btn.clicked.connect(self._reset_form)
        action_bar.addWidget(self.reset_btn)

        action_bar.addStretch()

        self.submit_btn = QtWidgets.QPushButton("🚀 立即执行任务 (Run)")
        self.submit_btn.setObjectName("primaryButton")
        self.submit_btn.setFixedHeight(36)
        self.submit_btn.setStyleSheet("font-size: 14px; font-weight: 700; padding: 0 24px;")
        self.submit_btn.clicked.connect(self._on_submit)
        action_bar.addWidget(self.submit_btn)

        form_layout.addLayout(action_bar)
        split_layout.addWidget(form_panel, 7)

        # === 右栏：命令元数据与帮助侧栏 ===
        info_panel = QtWidgets.QWidget()
        info_panel.setObjectName("cardPanel")
        info_panel.setFixedWidth(320)
        info_layout = QtWidgets.QVBoxLayout(info_panel)
        info_layout.setContentsMargins(16, 16, 16, 16)
        info_layout.setSpacing(12)

        info_title = QtWidgets.QLabel("ℹ️ 命令与插件信息")
        info_title.setStyleSheet("font-weight: 700; font-size: 14px; color: #0f172a;")
        info_layout.addWidget(info_title)

        self.info_desc_lbl = QtWidgets.QLabel("")
        self.info_desc_lbl.setObjectName("mutedText")
        self.info_desc_lbl.setWordWrap(True)
        info_layout.addWidget(self.info_desc_lbl)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        line.setStyleSheet("color: #e2e8f0;")
        info_layout.addWidget(line)

        # 属性清单
        self.meta_props_layout = QtWidgets.QFormLayout()
        self.meta_props_layout.setSpacing(8)

        self.lbl_manifest_ver = QtWidgets.QLabel("-")
        self.lbl_category = QtWidgets.QLabel("-")
        self.lbl_concurrency = QtWidgets.QLabel("-")
        self.lbl_filesystem = QtWidgets.QLabel("-")
        self.lbl_compat = QtWidgets.QLabel("-")

        self.meta_props_layout.addRow("插件版本:", self.lbl_manifest_ver)
        self.meta_props_layout.addRow("所属分类:", self.lbl_category)
        self.meta_props_layout.addRow("并发模式:", self.lbl_concurrency)
        self.meta_props_layout.addRow("文件隔离:", self.lbl_filesystem)
        self.meta_props_layout.addRow("Core 兼容:", self.lbl_compat)
        info_layout.addLayout(self.meta_props_layout)

        info_layout.addStretch()

        # 提示盒子
        tip_box = QtWidgets.QWidget()
        tip_box.setStyleSheet("background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 8px;")
        tip_layout = QtWidgets.QVBoxLayout(tip_box)
        tip_layout.setContentsMargins(8, 8, 8, 8)
        tip_icon_lbl = QtWidgets.QLabel("💡 提示与规范")
        tip_icon_lbl.setStyleSheet("font-weight: 700; color: #166534; font-size: 12px;")
        tip_content = QtWidgets.QLabel(
            "• 参数经 Schema 严格校验\n"
            "• 敏感参数由 Runtime 自动脱敏\n"
            "• 生成文件统一保存在独立工作区"
        )
        tip_content.setStyleSheet("color: #15803d; font-size: 11px;")
        tip_layout.addWidget(tip_icon_lbl)
        tip_layout.addWidget(tip_content)
        info_layout.addWidget(tip_box)

        split_layout.addWidget(info_panel, 3)
        main_layout.addLayout(split_layout, 1)

    def load_command(self, command_name: str, preset_params: dict | None = None):
        self.current_command = command_name
        self.current_manifest = self.runtime.get_command(command_name)
        self.current_schema = self.runtime.get_command_schema(command_name)

        self.cmd_title_lbl.setText(f"命令: {command_name}")
        if self.current_manifest:
            self.plugin_badge.setText(f"{self.current_manifest.name} v{self.current_manifest.version}")
            self.lbl_manifest_ver.setText(self.current_manifest.version)
            self.lbl_category.setText(self.current_manifest.category.upper())
            caps = self.current_manifest.capabilities or {}
            self.lbl_concurrency.setText("支持并发" if caps.get("concurrency", True) else "仅串行执行")
            self.lbl_filesystem.setText(caps.get("filesystem", "output-only"))
            self.lbl_compat.setText(self.current_manifest.core_compatibility or "*")

            cmd_desc = ""
            for cmd_obj in self.current_manifest.commands:
                if cmd_obj.name == command_name:
                    cmd_desc = cmd_obj.description
                    break
            self.info_desc_lbl.setText(cmd_desc or self.current_manifest.description)

        # 动态重建 Schema 表单
        if self.form_widget:
            self.form_container_layout.removeWidget(self.form_widget)
            self.form_widget.deleteLater()
            self.form_widget = None

        self.form_widget = DynamicSchemaForm(self.current_schema, command_name=command_name)
        if preset_params:
            self.form_widget.set_values(preset_params)
        self.form_container_layout.addWidget(self.form_widget)

    def _reset_form(self):
        if self.current_command:
            self.load_command(self.current_command)

    def _on_submit(self):
        if not self.form_widget:
            return
        valid, err = self.form_widget.validate_locally()
        if not valid:
            QtWidgets.QMessageBox.warning(self, "参数校验未通过", f"请检查输入参数:\n{err}")
            return
        params = self.form_widget.get_values()
        self.executeRequested.emit(self.current_command, params)


# ==============================================================================
# 视图 3：执行中工作区 (Execution Workspace)
# ==============================================================================

class RunningWorkspaceView(QtWidgets.QWidget):
    """
    任务执行中工作区视图：
    展示任务元数据、执行中动画、状态指示、脱敏参数摘要
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(20)

        # 顶部标题
        title_box = QtWidgets.QVBoxLayout()
        title_lbl = QtWidgets.QLabel("执行工作区 (Running Workspace)")
        title_lbl.setObjectName("pageTitle")
        self.sub_title_lbl = QtWidgets.QLabel("正在调度 Plugin Host 执行任务...")
        self.sub_title_lbl.setObjectName("mutedText")
        title_box.addWidget(title_lbl)
        title_box.addWidget(self.sub_title_lbl)
        layout.addLayout(title_box)

        # 核心状态卡片
        card = QtWidgets.QWidget()
        card.setObjectName("cardPanel")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(16)

        # 动态状态与进度
        status_bar = QtWidgets.QHBoxLayout()
        self.spinner_lbl = QtWidgets.QLabel("⏳")
        self.spinner_lbl.setStyleSheet("font-size: 28px;")
        status_bar.addWidget(self.spinner_lbl)

        status_text_box = QtWidgets.QVBoxLayout()
        self.status_title_lbl = QtWidgets.QLabel("任务正在运行中 (RUNNING)")
        self.status_title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #2563eb;")
        self.status_desc_lbl = QtWidgets.QLabel("Plugin Host 正在子进程沙箱中处理，请稍候...")
        self.status_desc_lbl.setObjectName("mutedText")
        status_text_box.addWidget(self.status_title_lbl)
        status_text_box.addWidget(self.status_desc_lbl)
        status_bar.addLayout(status_text_box)
        status_bar.addStretch()

        card_layout.addLayout(status_bar)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 0)  # 脉冲无边界动画
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        card_layout.addWidget(self.progress_bar)

        # 任务信息表格
        meta_form = QtWidgets.QFormLayout()
        meta_form.setSpacing(10)

        self.lbl_cmd = QtWidgets.QLabel("-")
        self.lbl_plugin = QtWidgets.QLabel("-")
        self.lbl_started_at = QtWidgets.QLabel("-")

        meta_form.addRow("执行命令:", self.lbl_cmd)
        meta_form.addRow("负责插件:", self.lbl_plugin)
        meta_form.addRow("启动时间:", self.lbl_started_at)
        card_layout.addLayout(meta_form)

        # 脱敏参数摘要
        params_group = QtWidgets.QGroupBox("提交参数摘要 (Parameters Preview)")
        params_layout = QtWidgets.QVBoxLayout(params_group)
        self.params_preview_txt = QtWidgets.QPlainTextEdit()
        self.params_preview_txt.setReadOnly(True)
        self.params_preview_txt.setFixedHeight(140)
        self.params_preview_txt.setObjectName("codeOutput")
        params_layout.addWidget(self.params_preview_txt)
        card_layout.addWidget(params_group)

        layout.addWidget(card)
        layout.addStretch()

    def start_running(self, command: str, params: dict, manifest: Any):
        self.lbl_cmd.setText(command)
        if manifest:
            self.lbl_plugin.setText(f"{manifest.name} (v{manifest.version})")
        else:
            self.lbl_plugin.setText("-")
        self.lbl_started_at.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.params_preview_txt.setPlainText(json.dumps(params, ensure_ascii=False, indent=2))


# ==============================================================================
# 视图 4：任务结果详情页 (Task Result & Export)
# ==============================================================================

class TaskResultDetailView(QtWidgets.QWidget):
    """
    任务结果详情页：
    全面支持六种状态展示：成功 (SUCCEEDED)、失败 (FAILED)、警告 (WARNING)、取消 (CANCELLED)、异常中断 (ABANDONED)、空结果 (EMPTY)。
    支持输出文件列表、一键导出产物、数据摘要卡片、原始日志折叠、修改参数重新执行。
    """
    reExecuteRequested = QtCore.Signal(str, dict)  # command_name, params
    backToHistory = QtCore.Signal()
    openAnnotation = QtCore.Signal(str)           # image_path

    def __init__(self, runtime: Runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.current_task_id: str = ""
        self.current_task_info: dict = {}
        self.current_result_obj: Any = None
        self._init_ui()

    def _init_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(24, 16, 24, 20)
        main_layout.setSpacing(14)

        # 顶部返回与操作条
        top_bar = QtWidgets.QHBoxLayout()
        self.btn_back = QtWidgets.QPushButton("← 返回历史列表")
        self.btn_back.setObjectName("secondaryButton")
        self.btn_back.clicked.connect(self.backToHistory.emit)
        top_bar.addWidget(self.btn_back)

        self.title_task_id_lbl = QtWidgets.QLabel("任务详情")
        self.title_task_id_lbl.setObjectName("pageTitle")
        top_bar.addWidget(self.title_task_id_lbl)

        top_bar.addStretch()

        self.btn_re_execute = QtWidgets.QPushButton("🔄 修改参数并重新执行")
        self.btn_re_execute.setObjectName("secondaryButton")
        self.btn_re_execute.clicked.connect(self._on_re_execute)
        top_bar.addWidget(self.btn_re_execute)

        main_layout.addLayout(top_bar)

        # 主滚动区域
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        container = QtWidgets.QWidget()
        self.content_layout = QtWidgets.QVBoxLayout(container)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(14)

        # 1. 主状态横幅卡片 (Banner)
        self.status_banner = QtWidgets.QFrame()
        self.status_banner.setObjectName("cardPanel")
        banner_layout = QtWidgets.QHBoxLayout(self.status_banner)
        banner_layout.setContentsMargins(18, 16, 18, 16)
        banner_layout.setSpacing(14)

        self.status_icon_lbl = QtWidgets.QLabel("✓")
        self.status_icon_lbl.setStyleSheet("font-size: 32px;")
        banner_layout.addWidget(self.status_icon_lbl)

        status_text_layout = QtWidgets.QVBoxLayout()
        status_text_layout.setSpacing(4)
        self.status_main_text = QtWidgets.QLabel("任务执行成功")
        self.status_main_text.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.status_sub_text = QtWidgets.QLabel("")
        self.status_sub_text.setObjectName("mutedText")
        status_text_layout.addWidget(self.status_main_text)
        status_text_layout.addWidget(self.status_sub_text)
        banner_layout.addLayout(status_text_layout, 1)

        self.duration_lbl = QtWidgets.QLabel("耗时: -")
        self.duration_lbl.setObjectName("tagLabelMuted")
        banner_layout.addWidget(self.duration_lbl)

        self.content_layout.addWidget(self.status_banner)

        # 2. 失败/异常诊断区域 (仅失败/中断时显示)
        self.diagnostic_box = QtWidgets.QWidget()
        self.diagnostic_box.setObjectName("diagnosticBox")
        diag_layout = QtWidgets.QVBoxLayout(self.diagnostic_box)
        diag_layout.setContentsMargins(16, 14, 16, 14)
        diag_layout.setSpacing(8)

        diag_title = QtWidgets.QLabel("🚨 错误诊断与处理建议 (Error Diagnostics)")
        diag_title.setObjectName("diagnosticTitle")
        diag_layout.addWidget(diag_title)

        self.diag_reason_lbl = QtWidgets.QLabel("")
        self.diag_reason_lbl.setObjectName("diagnosticReason")
        self.diag_reason_lbl.setWordWrap(True)
        diag_layout.addWidget(self.diag_reason_lbl)

        self.diag_advice_lbl = QtWidgets.QLabel("")
        self.diag_advice_lbl.setObjectName("diagnosticAdvice")
        self.diag_advice_lbl.setWordWrap(True)
        diag_layout.addWidget(self.diag_advice_lbl)

        self.content_layout.addWidget(self.diagnostic_box)

        # 3. 输出文件列表卡片 (Output Files)
        self.files_box = QtWidgets.QGroupBox("产物文件列表 (Output Files)")
        files_box_layout = QtWidgets.QVBoxLayout(self.files_box)
        files_box_layout.setContentsMargins(14, 14, 14, 14)
        files_box_layout.setSpacing(10)

        self.files_table = QtWidgets.QTableWidget(0, 4)
        self.files_table.setHorizontalHeaderLabels(["文件名", "相对路径", "文件大小", "操作"])
        self.files_table.horizontalHeader().setStretchLastSection(False)
        self.files_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.files_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.files_table.setFixedHeight(150)
        files_box_layout.addWidget(self.files_table)

        self.files_empty_lbl = QtWidgets.QLabel("本任务未产生输出文件。")
        self.files_empty_lbl.setObjectName("mutedText")
        self.files_empty_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        files_box_layout.addWidget(self.files_empty_lbl)

        self.content_layout.addWidget(self.files_box)

        # 4. 任务结果数据摘要 (Data Summary)
        self.data_summary_box = QtWidgets.QGroupBox("结果数据摘要 (Data Summary)")
        data_layout = QtWidgets.QVBoxLayout(self.data_summary_box)
        self.data_summary_txt = QtWidgets.QPlainTextEdit()
        self.data_summary_txt.setReadOnly(True)
        self.data_summary_txt.setFixedHeight(120)
        self.data_summary_txt.setObjectName("codeOutput")
        data_layout.addWidget(self.data_summary_txt)
        self.content_layout.addWidget(self.data_summary_box)

        # 5. 脱敏参数摘要 (Redacted Parameters)
        params_box = QtWidgets.QGroupBox("任务脱敏参数 (Redacted Parameters)")
        params_layout = QtWidgets.QVBoxLayout(params_box)
        self.params_txt = QtWidgets.QPlainTextEdit()
        self.params_txt.setReadOnly(True)
        self.params_txt.setFixedHeight(100)
        self.params_txt.setObjectName("codeOutput")
        params_layout.addWidget(self.params_txt)
        self.content_layout.addWidget(params_box)

        # 6. 任务报告与执行日志折叠 (Report & Logs)
        logs_container = QtWidgets.QWidget()
        logs_container.setObjectName("cardPanel")
        logs_layout = QtWidgets.QVBoxLayout(logs_container)
        logs_layout.setContentsMargins(14, 12, 14, 12)
        logs_layout.setSpacing(8)

        self.btn_toggle_logs = QtWidgets.QPushButton("▶ 展开任务执行报告与日志 (Report & Logs)")
        self.btn_toggle_logs.setObjectName("secondaryButton")
        self.btn_toggle_logs.setCheckable(True)
        self.btn_toggle_logs.setChecked(False)

        self.logs_content_widget = QtWidgets.QWidget()
        logs_inner_layout = QtWidgets.QVBoxLayout(self.logs_content_widget)
        logs_inner_layout.setContentsMargins(0, 8, 0, 0)
        self.report_txt = QtWidgets.QPlainTextEdit()
        self.report_txt.setReadOnly(True)
        self.report_txt.setFixedHeight(180)
        self.report_txt.setObjectName("logOutput")
        logs_inner_layout.addWidget(self.report_txt)

        self.logs_content_widget.setVisible(False)
        self.btn_toggle_logs.toggled.connect(lambda c: self.logs_content_widget.setVisible(c))
        self.btn_toggle_logs.toggled.connect(lambda c: self.btn_toggle_logs.setText("▼ 收起任务执行报告与日志" if c else "▶ 展开任务执行报告与日志 (Report & Logs)"))

        logs_layout.addWidget(self.btn_toggle_logs)
        logs_layout.addWidget(self.logs_content_widget)
        self.content_layout.addWidget(logs_container)

        self.content_layout.addStretch()
        scroll.setWidget(container)
        main_layout.addWidget(scroll, 1)

    def display_task(self, task_id: str, direct_result: Any = None, elapsed: float | None = None):
        self.current_task_id = task_id
        self.title_task_id_lbl.setText(f"任务详情: {task_id}")

        task_record = self.runtime.get_task(task_id) or {}
        self.current_task_info = task_record

        # 获取 Result
        result_dict = {}
        if direct_result is not None:
            self.current_result_obj = direct_result
            result_dict = direct_result.to_dict() if hasattr(direct_result, "to_dict") else direct_result
        else:
            result_dict = self.runtime.get_task_result(task_id) or {}

        status = task_record.get("status", "UNKNOWN").upper()
        if not status or status == "UNKNOWN":
            status = result_dict.get("status", "UNKNOWN").upper()

        # 计算耗时
        duration_text = "-"
        if elapsed is not None:
            duration_text = f"耗时: {elapsed:.2f}s"
        else:
            s_at = task_record.get("started_at")
            f_at = task_record.get("finished_at")
            if s_at and f_at:
                try:
                    t1 = datetime.fromisoformat(s_at)
                    t2 = datetime.fromisoformat(f_at)
                    duration_text = f"耗时: {(t2 - t1).total_seconds():.2f}s"
                except Exception:
                    pass
        self.duration_lbl.setText(duration_text)

        # 状态横幅渲染
        self._render_status_banner(status, result_dict, task_record)

        # 产物文件列表渲染
        files = result_dict.get("files") or []
        self._render_files_table(task_id, files)

        # 数据摘要
        data_val = result_dict.get("data")
        if data_val:
            self.data_summary_txt.setPlainText(json.dumps(data_val, ensure_ascii=False, indent=2))
            self.data_summary_box.setVisible(True)
        else:
            self.data_summary_txt.setPlainText("无结构化返回数据")
            self.data_summary_box.setVisible(False)

        # 脱敏参数渲染
        params = task_record.get("params") or {}
        self.params_txt.setPlainText(json.dumps(params, ensure_ascii=False, indent=2))

        # 报告渲染
        msg = result_dict.get("message", "")
        err_code = task_record.get("error_code")
        warnings = result_dict.get("warnings", [])
        report_content = f"--- 任务报告 [{task_id}] ---\n状态: {status}\n命令: {task_record.get('command')}\n消息: {msg}\n"
        if err_code:
            report_content += f"错误码: {err_code}\n"
        if warnings:
            report_content += f"警告: {warnings}\n"

        diagnostics = result_dict.get("data") or {}
        host_stderr = diagnostics.get("host_stderr")
        task_log_tail = diagnostics.get("task_log_tail")
        if host_stderr:
            report_content += f"\n--- Host 错误输出 ---\n{host_stderr.rstrip()}\n"

        # Read the task log from the workspace as the source of truth. Failed
        # results also carry a tail in result.data for CLI diagnostics, but a
        # successful task does not need to duplicate its log into result.json.
        task_log = ""
        workspace_path = task_record.get("workspace_path")
        if workspace_path:
            log_path = Path(workspace_path) / "logs" / "task.log"
            if log_path.is_file():
                try:
                    task_log = log_path.read_text(encoding="utf-8", errors="replace")[-8_000:]
                except OSError:
                    task_log = ""
        if not task_log:
            task_log = task_log_tail or ""
        if task_log:
            report_content += f"\n--- 插件执行日志（末尾） ---\n{task_log.rstrip()}\n"
        if not host_stderr and not task_log:
            report_content += "\n--- 插件执行日志 ---\n当前任务没有可显示的插件日志。\n"
        self.report_txt.setPlainText(report_content)

    @staticmethod
    def _apply_style_id(widget: QtWidgets.QWidget, style_id: str) -> None:
        """Refresh QSS after changing a status-specific object selector."""
        widget.setObjectName(style_id)
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _render_status_banner(self, status: str, result_dict: dict, task_record: dict):
        self.diagnostic_box.setVisible(False)

        if status == "SUCCEEDED":
            self.status_icon_lbl.setText("✅")
            self.status_main_text.setText("任务执行成功 (SUCCEEDED)")
            self._apply_style_id(self.status_main_text, "statusSuccess")
            self.status_sub_text.setText(result_dict.get("message") or "所有步骤已顺利完成，产物已落盘。")
            self._apply_style_id(self.status_banner, "statusBannerSuccess")

        elif status == "WARNING":
            self.status_icon_lbl.setText("⚠️")
            self.status_main_text.setText("执行完成但包含警告 (WARNING)")
            self._apply_style_id(self.status_main_text, "statusWarning")
            self.status_sub_text.setText(result_dict.get("message") or "任务产物已生成，但存在部分警告提示。")
            self._apply_style_id(self.status_banner, "statusBannerWarning")

        elif status == "FAILED":
            self.status_icon_lbl.setText("❌")
            self.status_main_text.setText("任务执行失败 (FAILED)")
            self._apply_style_id(self.status_main_text, "statusFailed")
            err_msg = result_dict.get("message") or "插件执行过程中遇到错误。"
            self.status_sub_text.setText(err_msg)
            self._apply_style_id(self.status_banner, "statusBannerFailed")

            # 错误诊断与建议
            self.diagnostic_box.setVisible(True)
            err_code = task_record.get("error_code") or "PLUGIN_ERROR"
            self.diag_reason_lbl.setText(f"【错误码: {err_code}】 {err_msg}")
            self.diag_advice_lbl.setText("建议操作: 检查输入参数格式、源文件有效性或依赖项是否完备后，点击右上角【修改参数并重新执行】。")

        elif status == "ABANDONED":
            self.status_icon_lbl.setText("⚠️")
            self.status_main_text.setText("任务异常中断 (ABANDONED)")
            self._apply_style_id(self.status_main_text, "statusAbandoned")
            self.status_sub_text.setText("任务宿主进程非正常退出或超时中断。")
            self._apply_style_id(self.status_banner, "statusBannerAbandoned")

            self.diagnostic_box.setVisible(True)
            self.diag_reason_lbl.setText(f"【错误码: {task_record.get('error_code') or 'HOST_INTERRUPTED'}】 进程执行中断")
            self.diag_advice_lbl.setText("建议操作: 查看日志确定插件是否因内存不足、超时或崩溃退出，随后重试。")

        elif status == "CANCELLED":
            self.status_icon_lbl.setText("⏹️")
            self.status_main_text.setText("任务已取消 (CANCELLED)")
            self._apply_style_id(self.status_main_text, "statusCancelled")
            self.status_sub_text.setText("用户已取消该任务执行。")
            self._apply_style_id(self.status_banner, "statusBannerCancelled")

        else:
            self.status_icon_lbl.setText("❓")
            self.status_main_text.setText(f"状态: {status}")
            self.status_sub_text.setText(result_dict.get("message") or "")

    def _render_files_table(self, task_id: str, files: list[str]):
        self.files_table.setRowCount(0)
        if not files:
            self.files_table.setVisible(False)
            self.files_empty_lbl.setVisible(True)
            return

        self.files_table.setVisible(True)
        self.files_empty_lbl.setVisible(False)
        self.files_table.setRowCount(len(files))

        workspace_path = self.current_task_info.get("workspace_path")

        for row, rel_path in enumerate(files):
            file_name = Path(rel_path).name
            full_file_path = Path(workspace_path) / "output" / rel_path if workspace_path else None

            # 1. 文件名
            name_item = QtWidgets.QTableWidgetItem(f"📄 {file_name}")
            name_item.setToolTip(rel_path)
            self.files_table.setItem(row, 0, name_item)

            # 2. 相对路径
            path_item = QtWidgets.QTableWidgetItem(rel_path)
            self.files_table.setItem(row, 1, path_item)

            # 3. 大小
            size_str = "-"
            if full_file_path and full_file_path.exists():
                sz = full_file_path.stat().st_size / 1024.0
                size_str = f"{sz:.1f} KB" if sz < 1024 else f"{(sz/1024):.2f} MB"
            size_item = QtWidgets.QTableWidgetItem(size_str)
            self.files_table.setItem(row, 2, size_item)

            # 4. 操作按钮栏 (导出产物 / 标注图片)
            actions_widget = QtWidgets.QWidget()
            act_layout = QtWidgets.QHBoxLayout(actions_widget)
            act_layout.setContentsMargins(4, 2, 4, 2)
            act_layout.setSpacing(6)

            export_btn = QtWidgets.QPushButton("💾 导出...")
            export_btn.setObjectName("smallButton")
            export_btn.clicked.connect(lambda _, rp=rel_path, fn=file_name: self._export_single_file(task_id, rp, fn))
            act_layout.addWidget(export_btn)

            # 如果是图片，提供标注入口
            if file_name.lower().endswith((".png", ".jpg", ".jpeg")):
                annot_btn = QtWidgets.QPushButton("✏️ 标注")
                annot_btn.setObjectName("smallButton")
                annot_btn.clicked.connect(lambda _, fp=str(full_file_path): self.openAnnotation.emit(fp))
                act_layout.addWidget(annot_btn)

            self.files_table.setCellWidget(row, 3, actions_widget)

    def _export_single_file(self, task_id: str, rel_path: str, filename: str):
        """调用 Runtime.commit_output 导出文件到用户选定路径，绝不在 UI 中私自复制"""
        dest_path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出任务产物", filename)
        if not dest_path:
            return
        try:
            self.runtime.commit_output(task_id, rel_path, Path(dest_path))
            QtWidgets.QMessageBox.information(self, "导出成功", f"文件已成功导出至:\n{dest_path}")
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "导出失败", f"导出文件时发生异常:\n{error}")

    def _on_re_execute(self):
        cmd = self.current_task_info.get("command")
        params = self.current_task_info.get("params") or {}
        if cmd:
            self.reExecuteRequested.emit(cmd, params)


# ==============================================================================
# 视图 5：任务历史 (Task History) - 仅通过 Runtime 访问
# ==============================================================================

class TaskHistoryView(QtWidgets.QWidget):
    """
    任务历史页面：
    支持状态筛选、命令筛选、分页查询、任务 ID 搜索、一键查看详情。
    严格通过 Runtime.list_tasks 与 Runtime.count_tasks 访问，绝不直连 SQLite！
    """
    taskSelected = QtCore.Signal(str)  # task_id
    reExecuteRequested = QtCore.Signal(str, dict)

    def __init__(self, runtime: Runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.page_size = 20
        self.current_page = 0
        self.total_count = 0
        self._init_ui()

    def _init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # 头部标题
        header = QtWidgets.QHBoxLayout()
        title_box = QtWidgets.QVBoxLayout()
        title_lbl = QtWidgets.QLabel("任务历史 (Task History)")
        title_lbl.setObjectName("pageTitle")
        sub_lbl = QtWidgets.QLabel("查看由 Runtime 记录的任务执行工作区、状态及结果")
        sub_lbl.setObjectName("mutedText")
        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)
        header.addLayout(title_box)
        header.addStretch()

        refresh_btn = QtWidgets.QPushButton("🔄 刷新")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.clicked.connect(self.refresh_data)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # 筛选工具栏
        filter_bar = QtWidgets.QHBoxLayout()
        filter_bar.setSpacing(10)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索任务 ID...")
        self.search_input.textChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.search_input, 2)

        self.status_combo = QtWidgets.QComboBox()
        self.status_combo.addItem("全部状态 (All Status)", None)
        self.status_combo.addItem("✅ SUCCEEDED (成功)", "SUCCEEDED")
        self.status_combo.addItem("❌ FAILED (失败)", "FAILED")
        self.status_combo.addItem("⚠️ ABANDONED (中断)", "ABANDONED")
        self.status_combo.addItem("⏹️ CANCELLED (取消)", "CANCELLED")
        self.status_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.status_combo, 1)

        self.command_combo = QtWidgets.QComboBox()
        self.command_combo.addItem("全部命令 (All Commands)", None)
        self.command_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.command_combo, 1)

        layout.addLayout(filter_bar)

        # 历史表格
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["任务 ID", "命令", "插件/版本", "状态", "启动时间", "操作"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        layout.addWidget(self.table, 1)

        # 分页与底部信息栏
        page_bar = QtWidgets.QHBoxLayout()
        page_bar.setSpacing(12)

        self.count_lbl = QtWidgets.QLabel("共 0 条任务记录")
        self.count_lbl.setObjectName("mutedText")
        page_bar.addWidget(self.count_lbl)

        page_bar.addStretch()

        self.btn_prev = QtWidgets.QPushButton("◀ 上一页")
        self.btn_prev.setObjectName("smallButton")
        self.btn_prev.clicked.connect(self._prev_page)

        self.page_lbl = QtWidgets.QLabel("第 1 页")
        self.page_lbl.setStyleSheet("font-weight: 600; color: #334155;")

        self.btn_next = QtWidgets.QPushButton("下一页 ▶")
        self.btn_next.setObjectName("smallButton")
        self.btn_next.clicked.connect(self._next_page)

        page_bar.addWidget(self.btn_prev)
        page_bar.addWidget(self.page_lbl)
        page_bar.addWidget(self.btn_next)

        layout.addLayout(page_bar)

    def populate_command_filter(self):
        current_cmd = self.command_combo.currentData()
        self.command_combo.blockSignals(True)
        self.command_combo.clear()
        self.command_combo.addItem("全部命令 (All Commands)", None)
        for cmd in sorted(self.runtime.list_commands().keys()):
            self.command_combo.addItem(cmd, cmd)
        if current_cmd:
            idx = self.command_combo.findData(current_cmd)
            if idx >= 0:
                self.command_combo.setCurrentIndex(idx)
        self.command_combo.blockSignals(False)

    def _on_filter_changed(self):
        self.current_page = 0
        self.refresh_data()

    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh_data()

    def _next_page(self):
        if (self.current_page + 1) * self.page_size < self.total_count:
            self.current_page += 1
            self.refresh_data()

    def refresh_data(self):
        self.populate_command_filter()

        status = self.status_combo.currentData()
        command = self.command_combo.currentData()
        offset = self.current_page * self.page_size

        try:
            # 严格调用 Runtime.list_tasks 与 count_tasks
            tasks = self.runtime.list_tasks(status=status, command=command, limit=self.page_size, offset=offset)
            self.total_count = self.runtime.count_tasks(status=status, command=command)
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "加载任务历史失败", f"调用 Runtime 获取任务异常:\n{error}")
            return

        # 客户端过滤任务 ID 搜索词
        query = self.search_input.text().strip().lower()
        if query:
            tasks = [t for t in tasks if query in t.get("id", "").lower()]

        self.table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            t_id = task.get("id", "")
            cmd = task.get("command", "")
            p_name = task.get("plugin_name", "")
            p_ver = task.get("plugin_version", "")
            t_status = task.get("status", "").upper()
            started_at = task.get("started_at", "")

            # 1. 任务 ID
            id_item = QtWidgets.QTableWidgetItem(t_id)
            id_item.setFont(QtGui.QFont("monospace", 11))
            self.table.setItem(row, 0, id_item)

            # 2. 命令
            cmd_item = QtWidgets.QTableWidgetItem(cmd)
            self.table.setItem(row, 1, cmd_item)

            # 3. 插件
            plugin_item = QtWidgets.QTableWidgetItem(f"{p_name} v{p_ver}")
            self.table.setItem(row, 2, plugin_item)

            # 4. 状态
            status_item = QtWidgets.QTableWidgetItem(t_status)
            if t_status == "SUCCEEDED":
                status_item.setForeground(QtGui.QColor("#10b981"))
            elif t_status == "FAILED":
                status_item.setForeground(QtGui.QColor("#ef4444"))
            elif t_status == "ABANDONED":
                status_item.setForeground(QtGui.QColor("#ea580c"))
            else:
                status_item.setForeground(QtGui.QColor("#64748b"))
            self.table.setItem(row, 3, status_item)

            # 5. 启动时间
            started_str = started_at.replace("T", " ").split(".")[0] if started_at else "-"
            start_item = QtWidgets.QTableWidgetItem(started_str)
            self.table.setItem(row, 4, start_item)

            # 6. 操作栏
            action_widget = QtWidgets.QWidget()
            a_layout = QtWidgets.QHBoxLayout(action_widget)
            a_layout.setContentsMargins(4, 2, 4, 2)
            a_layout.setSpacing(6)

            view_btn = QtWidgets.QPushButton("查看详情")
            view_btn.setObjectName("smallButton")
            view_btn.clicked.connect(lambda _, tid=t_id: self.taskSelected.emit(tid))
            a_layout.addWidget(view_btn)

            self.table.setCellWidget(row, 5, action_widget)

        # 分页状态更新
        max_page = max(1, (self.total_count + self.page_size - 1) // self.page_size)
        self.page_lbl.setText(f"第 {self.current_page + 1} / {max_page} 页")
        self.count_lbl.setText(f"共 {self.total_count} 条记录")
        self.btn_prev.setEnabled(self.current_page > 0)
        self.btn_next.setEnabled((self.current_page + 1) * self.page_size < self.total_count)

    def _on_cell_double_clicked(self, row: int, col: int):
        id_item = self.table.item(row, 0)
        if id_item:
            self.taskSelected.emit(id_item.text())


# ==============================================================================
# 视图 6：插件诊断与系统设置 (Plugins & Diagnostics)
# ==============================================================================

class PluginDiagnosticsView(QtWidgets.QWidget):
    """
    插件诊断与工作区清理页面：
    展示所有发现的插件元数据、命令索引、能力声明、工作区维护工具。
    仅调用 Runtime.list_plugins, Runtime.clean_workspace
    """
    def __init__(self, runtime: Runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self._init_ui()

    def _init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # 标题
        header = QtWidgets.QHBoxLayout()
        title_box = QtWidgets.QVBoxLayout()
        title_lbl = QtWidgets.QLabel("插件诊断与状态 (Plugins & Diagnostics)")
        title_lbl.setObjectName("pageTitle")
        sub_lbl = QtWidgets.QLabel("检查已加载插件的运行能力、清单规范及工作区存储维护")
        sub_lbl.setObjectName("mutedText")
        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)
        header.addLayout(title_box)
        header.addStretch()

        refresh_btn = QtWidgets.QPushButton("🔄 重新扫描插件")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.clicked.connect(self.refresh_plugins)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # 插件诊断表格
        self.plugins_table = QtWidgets.QTableWidget(0, 6)
        self.plugins_table.setHorizontalHeaderLabels(["插件名称", "版本", "分类", "命令列表", "并发支持", "隔离能力"])
        self.plugins_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.plugins_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.plugins_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.plugins_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.plugins_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.plugins_table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.plugins_table, 1)

        # 工作区维护工具箱
        clean_box = QtWidgets.QGroupBox("工作区维护与清理 (Workspace Maintenance)")
        clean_layout = QtWidgets.QHBoxLayout(clean_box)
        clean_layout.setContentsMargins(16, 16, 16, 16)
        clean_layout.setSpacing(12)

        clean_lbl = QtWidgets.QLabel("清理历史工作区:")
        clean_lbl.setStyleSheet("font-weight: 600;")
        clean_layout.addWidget(clean_lbl)

        self.date_picker = QtWidgets.QDateEdit()
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDate(QtCore.QDate.currentDate())
        clean_layout.addWidget(self.date_picker)

        clean_desc = QtWidgets.QLabel("之前的所有任务工作区目录")
        clean_desc.setObjectName("mutedText")
        clean_layout.addWidget(clean_desc)

        clean_layout.addStretch()

        self.btn_clean = QtWidgets.QPushButton("🧹 执行清理 (Clean)")
        self.btn_clean.setObjectName("secondaryButton")
        self.btn_clean.clicked.connect(self._on_clean_workspace)
        clean_layout.addWidget(self.btn_clean)

        layout.addWidget(clean_box)

        self.refresh_plugins()

    def refresh_plugins(self):
        try:
            plugins = self.runtime.list_plugins()
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "读取插件失败", f"Runtime list_plugins 异常:\n{error}")
            return

        self.plugins_table.setRowCount(len(plugins))
        for row, p in enumerate(plugins):
            # 1. 名称
            name_item = QtWidgets.QTableWidgetItem(f"📦 {p.name}")
            self.plugins_table.setItem(row, 0, name_item)

            # 2. 版本
            ver_item = QtWidgets.QTableWidgetItem(p.version)
            self.plugins_table.setItem(row, 1, ver_item)

            # 3. 分类
            cat_item = QtWidgets.QTableWidgetItem(p.category.upper())
            self.plugins_table.setItem(row, 2, cat_item)

            # 4. 命令列表
            cmd_names = [c.name for c in p.commands]
            cmds_item = QtWidgets.QTableWidgetItem(", ".join(cmd_names))
            self.plugins_table.setItem(row, 3, cmds_item)

            # 5. 并发
            caps = p.capabilities or {}
            conc_str = "✅ 支持" if caps.get("concurrency", True) else "🔒 串行"
            conc_item = QtWidgets.QTableWidgetItem(conc_str)
            self.plugins_table.setItem(row, 4, conc_item)

            # 6. 文件系统
            fs_str = caps.get("filesystem", "output-only")
            fs_item = QtWidgets.QTableWidgetItem(fs_str)
            self.plugins_table.setItem(row, 5, fs_item)

    def _on_clean_workspace(self):
        qdate = self.date_picker.date()
        target_date = date(qdate.year(), qdate.month(), qdate.day())

        reply = QtWidgets.QMessageBox.question(
            self,
            "确认清理工作区",
            f"将永久清理 {target_date.isoformat()} 之前的过期任务工作区文件。\n是否继续？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            try:
                cleaned_count = self.runtime.clean_workspace(target_date)
                QtWidgets.QMessageBox.information(self, "清理完成", f"已成功清理 {cleaned_count} 个过期工作区。")
            except Exception as error:
                QtWidgets.QMessageBox.critical(self, "清理失败", f"清理过程中发生异常:\n{error}")


# ==============================================================================
# 主窗口 (MainWindow)
# ==============================================================================

class MainWindow(QtWidgets.QMainWindow):
    """
    TestBox 现代主窗口：
    清晰划分为六大核心页面并由 Runtime 统一驱动：
    - Page 0: 工具目录 (ToolCatalogView)
    - Page 1: 命令配置与参数表单 (CommandDetailFormView)
    - Page 2: 执行工作区 (RunningWorkspaceView)
    - Page 3: 任务结果详情 (TaskResultDetailView)
    - Page 4: 任务历史 (TaskHistoryView)
    - Page 5: 插件诊断与状态 (PluginDiagnosticsView)
    """
    def __init__(self, root: Path | None = None):
        super().__init__()
        self._runtime_root = root
        self.runtime = Runtime(root)
        self.setWindowTitle("TestBox - 测试效能工具箱")
        self.resize(1120, 760)
        self.setMinimumSize(960, 640)

        self._init_ui()

    def _init_ui(self):
        root_widget = QtWidgets.QWidget()
        self.setCentralWidget(root_widget)

        root_layout = QtWidgets.QHBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. 侧边导航栏 (Sidebar)
        sidebar = QtWidgets.QWidget()
        sidebar.setObjectName("navSidebar")
        sidebar.setFixedWidth(220)
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 18, 12, 18)
        sidebar_layout.setSpacing(8)

        # Logo / Brand
        brand_bar = QtWidgets.QHBoxLayout()
        brand_bar.setSpacing(10)
        brand_icon = QtWidgets.QLabel("🧪")
        brand_icon.setStyleSheet("font-size: 22px;")
        brand_title = QtWidgets.QLabel("TestBox")
        brand_title.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 800; letter-spacing: 0.5px;")
        brand_bar.addWidget(brand_icon)
        brand_bar.addWidget(brand_title)
        brand_bar.addStretch()
        sidebar_layout.addLayout(brand_bar)

        sidebar_layout.addSpacing(14)

        # 导航列表
        self.nav_list = QtWidgets.QListWidget()
        self.nav_list.setObjectName("nav")
        self.nav_list.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self._add_nav_item("📦  工具目录", 0)
        self._add_nav_item("📋  任务历史", 4)
        self._add_nav_item("⚙️  插件与诊断", 5)

        self.nav_list.currentRowChanged.connect(self._on_nav_selected)
        sidebar_layout.addWidget(self.nav_list)

        sidebar_layout.addStretch()

        # 底部版本信息
        footer_lbl = QtWidgets.QLabel("TestBox Engine v1.0\nLocal-First Desktop")
        footer_lbl.setStyleSheet("color: #64748b; font-size: 11px; line-height: 1.4;")
        sidebar_layout.addWidget(footer_lbl)

        root_layout.addWidget(sidebar)

        # 2. 页面容器 (StackedWidget)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.setObjectName("mainStack")

        # 实例化子页面
        self.page_catalog = ToolCatalogView(self.runtime)
        self.page_form = CommandDetailFormView(self.runtime)
        self.page_running = RunningWorkspaceView()
        self.page_result = TaskResultDetailView(self.runtime)
        self.page_history = TaskHistoryView(self.runtime)
        self.page_diagnostics = PluginDiagnosticsView(self.runtime)

        self.stack.addWidget(self.page_catalog)      # 0
        self.stack.addWidget(self.page_form)         # 1
        self.stack.addWidget(self.page_running)      # 2
        self.stack.addWidget(self.page_result)       # 3
        self.stack.addWidget(self.page_history)      # 4
        self.stack.addWidget(self.page_diagnostics)  # 5

        root_layout.addWidget(self.stack, 1)

        # 信号绑定
        self.page_catalog.commandSelected.connect(self.navigate_to_command)
        self.page_form.backToCatalog.connect(lambda: self.switch_page(0))
        self.page_form.executeRequested.connect(self.execute_task)

        self.page_result.backToHistory.connect(lambda: self.switch_page(4))
        self.page_result.reExecuteRequested.connect(self.navigate_to_command)
        self.page_result.openAnnotation.connect(self.open_annotation_dialog)

        self.page_history.taskSelected.connect(self.navigate_to_task_result)

        # 默认高亮并打开工具目录
        self.nav_list.setCurrentRow(0)

    def _add_nav_item(self, text: str, page_index: int):
        item = QtWidgets.QListWidgetItem(text)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, page_index)
        self.nav_list.addItem(item)

    def _on_nav_selected(self, row: int):
        item = self.nav_list.item(row)
        if item:
            page_index = item.data(QtCore.Qt.ItemDataRole.UserRole)
            self.switch_page(page_index)

    def switch_page(self, index: int):
        self.stack.setCurrentIndex(index)
        # 页面刷新钩子
        if index == 0:
            self.page_catalog.reload_tools()
        elif index == 4:
            self.page_history.refresh_data()
        elif index == 5:
            self.page_diagnostics.refresh_plugins()

    def navigate_to_command(self, command_name: str, params: dict | None = None):
        self.page_form.load_command(command_name, params)
        self.stack.setCurrentIndex(1)

    def navigate_to_task_result(self, task_id: str):
        self.page_result.display_task(task_id)
        self.stack.setCurrentIndex(3)

    def execute_task(self, command_name: str, params: dict):
        manifest = self.runtime.get_command(command_name)
        self.page_running.start_running(command_name, params, manifest)
        self.stack.setCurrentIndex(2)

        # 启动后台 Worker
        # Keep the frozen-app root resolution inside Runtime. Passing
        # ``self.runtime.root`` here would turn a packaged GUI task into an
        # explicit source-root task and make it write into the executable
        # directory (often read-only on Windows).
        worker = RuntimeWorker(self._runtime_root, command_name, params)
        worker.signals.finished.connect(self._on_task_finished)
        worker.signals.failed.connect(self._on_task_failed)
        QtCore.QThreadPool.globalInstance().start(worker)

    def _on_task_finished(self, task_id: str, result: Any, elapsed: float):
        self.page_result.display_task(task_id, direct_result=result, elapsed=elapsed)
        self.stack.setCurrentIndex(3)

    def _on_task_failed(self, command: str, error_msg: str, elapsed: float):
        QtWidgets.QMessageBox.critical(self, "执行失败", f"任务执行遇到不可恢复的异常:\n{error_msg}")
        self.stack.setCurrentIndex(1)

    def open_annotation_dialog(self, image_path: str):
        dialog = AnnotationDialog(image_path, self)
        dialog.exec()

    def closeEvent(self, event: QtGui.QCloseEvent):
        self.runtime.close()
        super().closeEvent(event)


# ==============================================================================
# 视觉设计与样式表 (Apple & Modern Desktop QSS Design Tokens)
# ==============================================================================

STYLE = """
* {
    font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", Arial, sans-serif;
    font-size: 13px;
    color: #EDEDED;
}


/* 失败、诊断和日志必须在深色主题中保持高对比度；避免浅色背景叠加浅灰文字。 */
#statusBannerSuccess, #statusBannerWarning, #statusBannerFailed, #statusBannerAbandoned, #statusBannerCancelled {
    border-radius: 8px;
}
#statusBannerSuccess { background-color: #10251f; border: 1px solid #17624a; }
#statusBannerWarning { background-color: #2a2110; border: 1px solid #765718; }
#statusBannerFailed { background-color: #2b1418; border: 1px solid #7f2635; }
#statusBannerAbandoned { background-color: #2a1b0f; border: 1px solid #7c3b16; }
#statusBannerCancelled { background-color: #171b22; border: 1px solid #374151; }
#statusSuccess { color: #6ee7b7; font-size: 16px; font-weight: 700; }
#statusWarning { color: #fbbf24; font-size: 16px; font-weight: 700; }
#statusFailed { color: #fda4af; font-size: 16px; font-weight: 700; }
#statusAbandoned { color: #fdba74; font-size: 16px; font-weight: 700; }
#statusCancelled { color: #cbd5e1; font-size: 16px; font-weight: 700; }
#diagnosticBox { background-color: #2b1418; border: 1px solid #7f2635; border-radius: 8px; }
#diagnosticTitle { color: #fecdd3; font-size: 14px; font-weight: 700; }
#diagnosticReason { color: #fda4af; font-size: 13px; font-weight: 600; }
#diagnosticAdvice { color: #fecdd3; font-size: 12px; }
#codeOutput, #logOutput {
    background-color: #09090B;
    color: #EDEDED;
    border: 1px solid #27272A;
    font-family: "Cascadia Mono", Consolas, "Microsoft YaHei UI", monospace;
    font-size: 12px;
}
#logOutput { background-color: #111827; color: #F8FAFC; }

QMainWindow, QStackedWidget#mainStack, QScrollArea, QScrollArea > QWidget > QWidget {
    background-color: #000000;
}

/* 侧边导航栏 */
#navSidebar {
    background-color: #0A0A0C;
    border-right: 1px solid #222226;
}

#nav {
    background: transparent;
    border: none;
    outline: none;
}

#nav::item {
    color: #888888;
    min-height: 40px;
    padding-left: 14px;
    border-radius: 6px;
    margin-bottom: 4px;
    font-weight: 500;
}

#nav::item:hover {
    background-color: #161618;
    color: #FAFAFA;
}

#nav::item:selected {
    background-color: #18181B;
    color: #10B981;
    border: 1px solid #27272A;
    font-weight: 600;
}

/* 标题与文字样式 */
#pageTitle {
    font-size: 20px;
    font-weight: 700;
    color: #FAFAFA;
    letter-spacing: -0.3px;
}

#mutedText {
    color: #71717A;
    font-size: 12px;
}

#fieldErrorLabel {
    color: #F87171;
    font-size: 12px;
    font-weight: 600;
    margin-top: 2px;
}

#tagLabel {
    background-color: rgba(16, 185, 129, 0.12);
    color: #34D399;
    border: 1px solid rgba(16, 185, 129, 0.25);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}

#tagLabelMuted {
    background-color: #18181B;
    color: #A1A1AA;
    border: 1px solid #27272A;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 500;
}

/* 工具卡片 */
#toolCard {
    background-color: #0D0D10;
    border: 1px solid #222226;
    border-radius: 8px;
}

#toolCard:hover {
    border-color: #10B981;
    background-color: #121215;
}

#toolCardTitle {
    font-size: 15px;
    font-weight: 700;
    color: #FAFAFA;
}

#toolCardDesc {
    font-size: 12px;
    color: #A1A1AA;
    line-height: 1.4;
}

/* 面板卡片与 GroupBox */
#cardPanel, QGroupBox {
    background-color: #0D0D10;
    border: 1px solid #222226;
    border-radius: 8px;
}

QGroupBox {
    margin-top: 14px;
    font-weight: 600;
    color: #EDEDED;
    padding-top: 14px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    background-color: #000000;
    color: #A1A1AA;
    font-size: 12px;
}

/* 按钮规范 */
QPushButton {
    min-height: 32px;
    padding: 0 14px;
    border-radius: 6px;
    font-weight: 500;
    background-color: #18181B;
    color: #EDEDED;
    border: 1px solid #27272A;
}

QPushButton:hover {
    background-color: #222226;
    border-color: #3F3F46;
    color: #FAFAFA;
}

QPushButton:pressed {
    background-color: #141416;
}

#primaryButton {
    background-color: #10B981;
    color: #000000;
    border: 1px solid #10B981;
    font-weight: 700;
}

#primaryButton:hover {
    background-color: #34D399;
    border-color: #34D399;
    color: #000000;
}

#primaryButton:pressed {
    background-color: #059669;
    border-color: #059669;
    color: #000000;
}

#primaryButton:disabled {
    background-color: #27272A;
    border-color: #27272A;
    color: #71717A;
}

#secondaryButton {
    background-color: #18181B;
    color: #EDEDED;
    border: 1px solid #27272A;
}

#secondaryButton:hover {
    background-color: #222226;
    border-color: #3F3F46;
    color: #FAFAFA;
}

#smallButton {
    min-height: 26px;
    padding: 0 8px;
    font-size: 12px;
    background-color: #18181B;
    color: #EDEDED;
    border: 1px solid #27272A;
}

#smallButton:hover {
    background-color: #222226;
    border-color: #3F3F46;
    color: #FAFAFA;
}

/* 表单输入控件 */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {
    min-height: 32px;
    border: 1px solid #27272A;
    border-radius: 6px;
    padding: 0 8px;
    background-color: #09090B;
    color: #FAFAFA;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus {
    border: 1px solid #10B981;
    background-color: #09090B;
}

QComboBox QAbstractItemView {
    background-color: #121215;
    border: 1px solid #27272A;
    selection-background-color: #18181B;
    selection-color: #10B981;
    color: #FAFAFA;
    padding: 4px;
}

QPlainTextEdit, QTextEdit {
    border: 1px solid #27272A;
    border-radius: 6px;
    padding: 8px;
    background-color: #09090B;
    color: #EDEDED;
}

QPlainTextEdit:focus, QTextEdit:focus {
    border: 1px solid #10B981;
}

QProgressBar {
    background-color: #18181B;
    border: 1px solid #27272A;
    border-radius: 4px;
    text-align: center;
    color: #EDEDED;
}

QProgressBar::chunk {
    background-color: #10B981;
    border-radius: 3px;
}

/* 表格控件 */
QTableWidget {
    background-color: #0D0D10;
    border: 1px solid #222226;
    border-radius: 6px;
    gridline-color: #1A1A1E;
    selection-background-color: #18181B;
    selection-color: #10B981;
    color: #EDEDED;
}

QHeaderView::section {
    background-color: #121215;
    color: #A1A1AA;
    font-weight: 600;
    padding: 8px;
    border: none;
    border-bottom: 1px solid #27272A;
}

QListWidget {
    background-color: #09090B;
    border: 1px solid #27272A;
    border-radius: 6px;
    color: #EDEDED;
}

QListWidget::item:selected {
    background-color: #18181B;
    color: #10B981;
}

QScrollBar:vertical {
    border: none;
    background: #000000;
    width: 8px;
    margin: 0px 0 0px 0;
}
QScrollBar::handle:vertical {
    background: #27272A;
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #3F3F46;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    border: none;
    background: none;
}
"""


def main() -> None:
    # Use a Windows-native UI font and point-sized application font. This
    # avoids the blurry fallback produced by CSS pixel sizing on scaled
    # Windows displays while retaining the existing layout.
    if sys.platform == "win32":
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
        os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("TestBox")
    app_font = QtGui.QFont("Segoe UI" if sys.platform == "win32" else "Helvetica Neue")
    app_font.setPointSize(10)
    app_font.setStyleStrategy(QtGui.QFont.StyleStrategy.PreferQuality)
    app.setFont(app_font)
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
