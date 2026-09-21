from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from testbox.core.plugin_packages import PluginPackageError
from testbox.core.runtime import Runtime
from testbox.core.schema_validator import SchemaValidationError


# Source and legacy frozen launches can still expose the Host protocol from
# this module.  The packaged windowed GUI uses the console-mode
# ``TestBox-GUI-Host.exe`` companion from ProcessRunner instead, because a
# windowed Windows executable cannot provide a reliable stdout pipe.
# Handle this internal mode before importing or starting Qt.
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


# Schema 字段与枚举值统一使用“中文说明 + 英文键名/原始值”的展示方式。
# 英文键名仍保留，便于用户与 CLI、插件文档和任务结果对应。
PARAMETER_LABELS = {
    "count": "生成数量",
    "format": "输出格式",
    "seed": "随机种子",
    "template": "数据模板",
    "rules": "字段规则",
    "rule_set": "规则集文件",
    "source_file": "来源文件",
    "source_format": "来源格式",
    "txt_delimiter": "文本分隔符",
    "txt_header": "包含表头",
    "sql_dialect": "SQL 方言",
    "sql_table": "SQL 表名",
    "sql_batch_size": "SQL 批量大小",
    "sql_transaction": "使用事务",
    "zip_formats": "压缩包内格式",
    "input": "输入文件",
    "input_format": "字段清单格式",
    "dialect": "数据库类型 / SQL 方言",
    "include_constraints": "包含约束信息",
    "fail_on_unsupported": "遇到不支持语法时失败",
    "include_comments": "在 SELECT 中保留字段注释",
    "screenshots": "截图文件",
    "row_indexes": "截图对应行号",
    "existing_reports": "已有报告",
    "column_mapping": "列名映射",
    "status": "测试结果状态",
    "update_excel": "回写 Excel",
    "include_unmatched": "包含未匹配项",
    "interactive": "交互式截图",
    "image_width_inches": "报告图片宽度",
}

PARAMETER_DESCRIPTIONS = {
    "count": "要生成的测试数据条数。",
    "format": "生成文件的格式。",
    "seed": "固定后可复现相同结果；留空则使用随机种子。",
    "template": "选择内置测试数据模板。",
    "rules": "直接输入字段规则数组，适合高级场景。",
    "rule_set": "包含字段规则的 JSON/YAML 文件。",
    "source_file": "用于提取字段规则的 SQL、Excel 或文本文件。",
    "source_format": "来源文件的类型。",
    "txt_delimiter": "TXT 输出使用的字段分隔符。",
    "txt_header": "是否在 TXT 输出中写入字段名。",
    "sql_dialect": "生成 INSERT SQL 时使用的数据库方言。",
    "sql_table": "生成 INSERT SQL 使用的表名。",
    "sql_batch_size": "每批 INSERT 包含的记录数。",
    "sql_transaction": "是否用事务包裹批量 INSERT。",
    "zip_formats": "压缩包中要包含的输出格式列表。",
    "input": "选择要处理的输入文件。",
    "input_format": "输入字段清单的文件格式；自动检测可按扩展名判断。",
    "dialect": "决定 SQL 标识符的引用方式；自动检测可按内容判断。",
    "include_constraints": "是否导出主键、唯一键、外键等约束信息。",
    "fail_on_unsupported": "遇到无法识别的语法时直接失败，而不是仅给出警告。",
    "include_comments": "是否把字段注释作为 SELECT 列的注释保留。",
    "screenshots": "按待执行项顺序选择对应截图。",
    "row_indexes": "每张截图对应的 Excel 行号列表。",
    "existing_reports": "可选的已有 Word 报告，用于继续追加内容。",
    "column_mapping": "将业务角色映射到 Excel 表头名称。",
    "status": "回写到测试结果列中的状态文本。",
    "update_excel": "是否将识别或执行结果回写到原 Excel。",
    "include_unmatched": "是否允许截图数量少于待执行项；未匹配项会保留在索引警告中。",
    "interactive": "启动逐条执行面板，使用 F8 截图并进入标注窗口；需要桌面环境和屏幕录制权限。",
    "image_width_inches": "插入 Word 报告时的截图宽度，单位为英寸。",
}

ENUM_LABELS = {
    "format": {"json": "JSON（json）", "csv": "CSV（csv）", "xlsx": "Excel（xlsx）", "txt": "文本（txt）", "sql": "SQL（sql）", "zip": "压缩包（zip）"},
    "template": {"retail_customer": "零售客户（retail_customer）", "account": "账户（account）", "product": "商品（product）", "transaction": "交易（transaction）"},
    "source_format": {"sql": "SQL（sql）", "excel": "Excel（excel）"},
    "sql_dialect": {"mysql": "MySQL", "postgresql": "PostgreSQL", "sqlserver": "SQL Server", "oracle": "Oracle", "sqlite": "SQLite"},
    "input_format": {"auto": "自动检测（auto）", "json": "JSON（json）", "csv": "CSV（csv）", "xlsx": "Excel（xlsx）"},
    "dialect": {"auto": "自动检测（auto）", "mysql": "MySQL", "postgresql": "PostgreSQL", "sqlserver": "SQL Server", "oracle": "Oracle", "sqlite": "SQLite", "hudi": "Apache Hudi（hudi）", "hive": "Apache Hive（hive）", "hbase": "Apache HBase（hbase）", "maxcompute": "MaxCompute（maxcompute）", "mc": "MaxCompute 简写（mc）"},
}

class FormInputError(ValueError):
    """GUI input parsing error associated with a single Schema field."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


TYPE_LABELS = {
    "string": "文本",
    "integer": "整数",
    "number": "数字",
    "boolean": "开关",
    "array": "列表",
    "object": "对象",
}

PLUGIN_LABELS = {
    "data-generator": "测试数据生成器",
    "sql-parser": "SQL 字段解析器",
    "sql-select": "SQL 查询生成器",
    "evidence-tool": "测试证据工具",
}

CATEGORY_LABELS = {
    "data": "数据",
    "parser": "解析",
    "sql": "SQL",
    "evidence": "证据",
}

def _parameter_label(key: str) -> str:
    return PARAMETER_LABELS.get(key, key.replace("_", " ").capitalize())


def _parameter_description(key: str, spec: dict[str, Any]) -> str:
    return str(spec.get("description") or PARAMETER_DESCRIPTIONS.get(key) or "")


def _enum_label(key: str, value: Any) -> str:
    return ENUM_LABELS.get(key, {}).get(str(value), str(value))


APP_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
  <defs>
    <linearGradient id="tbBgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#111827"/>
      <stop offset="100%" stop-color="#0B0F17"/>
    </linearGradient>
    <linearGradient id="tbBorderGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#10B981" stop-opacity="0.6"/>
      <stop offset="100%" stop-color="#064E3B" stop-opacity="0.2"/>
    </linearGradient>
    <linearGradient id="gradCubeTop" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#34D399"/>
      <stop offset="100%" stop-color="#10B981"/>
    </linearGradient>
    <linearGradient id="gradCubeLeft" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#059669"/>
      <stop offset="100%" stop-color="#047857"/>
    </linearGradient>
    <linearGradient id="gradCubeRight" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#047857"/>
      <stop offset="100%" stop-color="#064E3B"/>
    </linearGradient>
  </defs>

  <rect x="8" y="8" width="112" height="112" rx="26" fill="url(#tbBgGrad)" stroke="url(#tbBorderGrad)" stroke-width="2.5"/>

  <circle cx="24" cy="24" r="1.8" fill="#10B981" opacity="0.4"/>
  <circle cx="104" cy="24" r="1.8" fill="#10B981" opacity="0.4"/>
  <circle cx="24" cy="104" r="1.8" fill="#10B981" opacity="0.4"/>
  <circle cx="104" cy="104" r="1.8" fill="#10B981" opacity="0.4"/>

  <polygon points="64,28 98,46 64,64 30,46" fill="url(#gradCubeTop)"/>
  <polygon points="64,34 90,48 64,61 38,48" fill="#6EE7B7" opacity="0.2"/>

  <polygon points="30,46 64,64 64,100 30,82" fill="url(#gradCubeLeft)"/>
  <polygon points="64,64 98,46 98,82 64,100" fill="url(#gradCubeRight)"/>

  <line x1="64" y1="28" x2="98" y2="46" stroke="#A7F3D0" stroke-width="1.5" stroke-opacity="0.7"/>
  <line x1="64" y1="28" x2="30" y2="46" stroke="#A7F3D0" stroke-width="1.5" stroke-opacity="0.7"/>
  <line x1="64" y1="64" x2="64" y2="100" stroke="#34D399" stroke-width="2" stroke-opacity="0.6"/>

  <path d="M41,68 L48,75 L56,62" fill="none" stroke="#FFFFFF" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>

  <line x1="74" y1="66" x2="88" y2="58" stroke="#34D399" stroke-width="2.5" stroke-linecap="round" stroke-opacity="0.9"/>
  <line x1="74" y1="76" x2="88" y2="68" stroke="#34D399" stroke-width="2.5" stroke-linecap="round" stroke-opacity="0.9"/>
  <line x1="74" y1="86" x2="82" y2="82" stroke="#34D399" stroke-width="2.5" stroke-linecap="round" stroke-opacity="0.6"/>
</svg>"""


def get_app_logo_pixmap(size: int = 32) -> QtGui.QPixmap:
    from PySide6 import QtSvg
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(APP_LOGO_SVG.encode("utf-8")))
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return pixmap


def get_app_logo_icon() -> QtGui.QIcon:
    icon = QtGui.QIcon()
    for s in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(get_app_logo_pixmap(s))
    return icon


def _file_dialog_options():
    # 使用 Qt 自绘文件选择器，避免 macOS/Windows 原生白底对话框绕过深色主题。
    return QtWidgets.QFileDialog.Option.DontUseNativeDialog


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
    """单个文件选择组件，支持手动输入或粘贴文件路径，并严格校验阻止输入目录地址"""
    valueChanged = QtCore.Signal(str)

    def __init__(self, placeholder: str = "点击选择或输入文件路径...", filter_str: str = "所有文件 (*.*)", parent=None):
        super().__init__(parent)
        self.filter_str = filter_str
        self._path = ""

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.line_edit = QtWidgets.QLineEdit()
        self.line_edit.setPlaceholderText(placeholder)
        self.line_edit.setReadOnly(False)
        self.line_edit.textEdited.connect(self._on_text_edited)
        self.line_edit.editingFinished.connect(self._on_editing_finished)

        self.btn_browse = QtWidgets.QPushButton("选择文件…")
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
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择文件", "", self.filter_str, options=_file_dialog_options())
        if file_path:
            self.set_path(file_path)

    def _on_text_edited(self, raw_text: str):
        self._validate_and_apply(raw_text.strip(), from_user_input=True)

    def _on_editing_finished(self):
        self._validate_and_apply(self.line_edit.text().strip(), from_user_input=True)

    def _validate_and_apply(self, raw_text: str, from_user_input: bool = False):
        clean_text = raw_text.strip().strip("'\"")
        if not clean_text:
            self._path = ""
            self.line_edit.setStyleSheet("")
            self.line_edit.setToolTip("")
            self.btn_clear.setVisible(False)
            self.valueChanged.emit("")
            return

        p = Path(clean_text)
        if p.is_dir():
            # 目录地址非法：重置有效路径并显示醒目警示
            self._path = ""
            self.line_edit.setStyleSheet("border: 1.5px solid #EF4444; background-color: #2b1418; color: #FCA5A5;")
            self.line_edit.setToolTip("❌ 不能输入目录地址，必须选择具体文件！")
            self.btn_clear.setVisible(True)
            self.valueChanged.emit("")
            return

        # 是文件或有效路径输入
        self._path = clean_text
        self.line_edit.setStyleSheet("")
        if p.exists() and p.is_file():
            size_kb = p.stat().st_size / 1024.0
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{(size_kb/1024):.2f} MB"
            self.line_edit.setToolTip(f"完整路径: {clean_text}\n大小: {size_str}")
        else:
            self.line_edit.setToolTip(f"文件路径: {clean_text}")
        self.btn_clear.setVisible(True)
        self.valueChanged.emit(self._path)

    def set_path(self, path: str):
        clean_path = path.strip().strip("'\"") if path else ""
        self.line_edit.blockSignals(True)
        self.line_edit.setText(clean_path)
        self.line_edit.blockSignals(False)
        self._validate_and_apply(clean_path, from_user_input=False)

    def get_path(self) -> str:
        return self._path

    def clear(self):
        self.set_path("")


class MultiFilesPicker(QtWidgets.QWidget):
    """多文件列表选择组件"""
    valueChanged = QtCore.Signal(list)

    def __init__(self, filter_str: str = "所有文件 (*.*)", parent=None):
        super().__init__(parent)
        self.filter_str = filter_str
        self._paths: list[str] = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        btn_bar = QtWidgets.QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_add = QtWidgets.QPushButton("➕ 添加文件…")
        self.btn_add.setObjectName("secondaryButton")
        self.btn_add.clicked.connect(self._add_files)

        self.btn_remove = QtWidgets.QPushButton("➖ 移除选中")
        self.btn_remove.setObjectName("smallButton")
        self.btn_remove.clicked.connect(self._remove_selected)

        self.btn_clear = QtWidgets.QPushButton("全部清空")
        self.btn_clear.setObjectName("smallButton")
        self.btn_clear.clicked.connect(self.clear)

        self.count_label = QtWidgets.QLabel("已选择 0 个文件")
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
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "选择多个文件", "", self.filter_str, options=_file_dialog_options())
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
        self.count_label.setText(f"已选择 {len(self._paths)} 个文件")
        self.valueChanged.emit(self._paths)




class DataMockFieldRow(QtWidgets.QWidget):
    """可编辑的一行自定义字段定义。"""
    removed = QtCore.Signal(object)

    GENERATORS = [
        ("按类型自动匹配", ""), ("自增序列", "sequence"), ("随机字符串", "string_random"),
        ("随机整数", "integer_random"), ("随机小数 / 金额", "decimal_random"),
        ("随机布尔值", "boolean_random"), ("随机日期", "date_random"),
        ("随机日期时间", "datetime_random"), ("中文姓名", "name_cn"), ("中国手机号", "mobile_cn"),
        ("邮箱", "email"), ("中国地址", "china_address"), ("枚举 / 权重枚举", "weighted_enum"),
        ("固定值", "constant"), ("模板文本", "template"), ("UUID", "uuid"), ("交易流水号", "transaction_id"),
    ]
    TYPES = ["VARCHAR(64)", "INT", "BIGINT", "DECIMAL(12,2)", "BOOLEAN", "DATE", "DATETIME", "TEXT", "UUID"]

    def __init__(self, value: dict | None = None, parent=None):
        super().__init__(parent)
        value = value or {}
        self.setObjectName("dataMockFieldRow")
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(5)
        self.name_edit = QtWidgets.QLineEdit(str(value.get("name", "")))
        self.name_edit.setPlaceholderText("字段名，例如 user_id")
        self.type_edit = QtWidgets.QComboBox(); self.type_edit.setEditable(True); self.type_edit.addItems(self.TYPES)
        self.type_edit.setCurrentText(str(value.get("type", "VARCHAR(64)")))
        self.generator_combo = QtWidgets.QComboBox()
        for label, key in self.GENERATORS: self.generator_combo.addItem(label, key)
        generator = str(value.get("generator", "")); index = self.generator_combo.findData(generator)
        self.generator_combo.setCurrentIndex(index if index >= 0 else 0)
        self.options_edit = QtWidgets.QLineEdit(); self.options_edit.setPlaceholderText('{"min":18,"max":60} / {"values":["正常","禁用"]}')
        options = value.get("options") or {}; self.options_edit.setText(json.dumps(options, ensure_ascii=False) if options else "")
        self.comment_edit = QtWidgets.QLineEdit(str(value.get("comment", ""))); self.comment_edit.setPlaceholderText("字段注释")
        self.unique_check = QtWidgets.QCheckBox("唯一"); self.unique_check.setChecked(bool(value.get("unique", False)))
        self.primary_check = QtWidgets.QCheckBox("主键"); self.primary_check.setChecked(bool(value.get("primary_key", False)))
        self.auto_increment_check = QtWidgets.QCheckBox("自增"); self.auto_increment_check.setChecked(bool(value.get("auto_increment", False)))
        self.nullable_spin = QtWidgets.QDoubleSpinBox(); self.nullable_spin.setRange(0, 1); self.nullable_spin.setSingleStep(0.05); self.nullable_spin.setDecimals(2); self.nullable_spin.setValue(float(value.get("nullable_rate", 0) or 0))
        self.remove_btn = QtWidgets.QPushButton("删除"); self.remove_btn.setObjectName("smallButton"); self.remove_btn.clicked.connect(lambda: self.removed.emit(self))
        for col, widget in enumerate((self.name_edit, self.type_edit, self.generator_combo, self.options_edit, self.comment_edit, self.unique_check, self.primary_check, self.auto_increment_check, QtWidgets.QLabel("空值率"), self.nullable_spin, self.remove_btn)): layout.addWidget(widget, 0, col)
        layout.setColumnStretch(0, 2); layout.setColumnStretch(2, 2); layout.setColumnStretch(3, 3); layout.setColumnStretch(4, 2)

    def get_value(self) -> dict:
        text = self.options_edit.text().strip()
        if text:
            try: options = json.loads(text)
            except json.JSONDecodeError as error: raise ValueError(f"字段 {self.name_edit.text().strip() or '未命名'} 的生成参数必须是 JSON 对象") from error
            if not isinstance(options, dict): raise ValueError(f"字段 {self.name_edit.text().strip() or '未命名'} 的生成参数必须是 JSON 对象")
        else: options = {}
        value = {"name": self.name_edit.text().strip(), "type": self.type_edit.currentText().strip() or "VARCHAR", "options": options, "unique": self.unique_check.isChecked(), "primary_key": self.primary_check.isChecked(), "auto_increment": self.auto_increment_check.isChecked(), "nullable_rate": self.nullable_spin.value()}
        if self.comment_edit.text().strip(): value["comment"] = self.comment_edit.text().strip()
        if self.generator_combo.currentData(): value["generator"] = self.generator_combo.currentData()
        return value


class DataMockFieldsEditor(QtWidgets.QWidget):
    """data.mock 专用的动态字段设计器，支持左右横向滚动以适应紧凑窗口。"""
    def __init__(self, values: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.rows: list[DataMockFieldRow] = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        heading = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("字段列表")
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        heading.addWidget(title)
        self.count_label = QtWidgets.QLabel("当前 0 个字段")
        self.count_label.setObjectName("mutedText")
        heading.addWidget(self.count_label)
        heading.addStretch()

        self.add_btn = QtWidgets.QPushButton("＋ 添加字段")
        self.add_btn.setObjectName("secondaryButton")
        self.add_btn.clicked.connect(self.add_row)
        heading.addWidget(self.add_btn)
        layout.addLayout(heading)

        hint = QtWidgets.QLabel('每行定义一个字段；生成参数填写 JSON，例如随机整数使用 {"min":18,"max":60}，枚举使用 {"values":["正常","禁用"]}。（支持左右横向滑动查看全部参数）')
        hint.setObjectName("mutedText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 嵌套横向可滚动的 ScrollArea，彻底解决小窗口横向挤压推飞按钮问题
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.inner_widget = QtWidgets.QWidget()
        self.inner_widget.setMinimumWidth(980)
        self.inner_layout = QtWidgets.QVBoxLayout(self.inner_widget)
        self.inner_layout.setContentsMargins(0, 0, 0, 0)
        self.inner_layout.setSpacing(6)

        header = QtWidgets.QGridLayout()
        header.setContentsMargins(8, 0, 8, 0)
        header.setHorizontalSpacing(8)
        header_labels = ["字段名", "数据库类型", "生成方式", "生成参数", "注释", "唯一", "主键", "自增", "空值率", "", "操作"]
        for col, text_h in enumerate(header_labels):
            lbl = QtWidgets.QLabel(text_h)
            lbl.setStyleSheet("font-weight: 600; color: #94a3b8; font-size: 12px;")
            header.addWidget(lbl, 0, col)
        header.setColumnStretch(0, 2)
        header.setColumnStretch(2, 2)
        header.setColumnStretch(3, 3)
        header.setColumnStretch(4, 2)
        self.inner_layout.addLayout(header)

        self.rows_layout = QtWidgets.QVBoxLayout()
        self.rows_layout.setSpacing(5)
        self.inner_layout.addLayout(self.rows_layout)

        self.empty_label = QtWidgets.QLabel("还没有字段，请点击“＋ 添加字段”开始设计表结构。")
        self.empty_label.setObjectName("mutedText")
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.inner_layout.addWidget(self.empty_label)
        self.inner_layout.addStretch()

        self.scroll_area.setWidget(self.inner_widget)
        layout.addWidget(self.scroll_area, 1)

        for value in values or []:
            self.add_row(value)
        self._sync_state()

    def add_row(self, value: dict | None = None):
        row = DataMockFieldRow(value, self)
        row.removed.connect(self.remove_row)
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self._sync_state()

    def remove_row(self, row):
        if row in self.rows:
            self.rows.remove(row)
            self.rows_layout.removeWidget(row)
            row.deleteLater()
            self._sync_state()

    def _sync_state(self):
        self.count_label.setText(f"当前 {len(self.rows)} 个字段")
        self.empty_label.setVisible(not self.rows)

    def get_values(self) -> list[dict]:
        return [row.get_value() for row in self.rows]

    def set_values(self, values: list[dict]):
        while self.rows:
            self.remove_row(self.rows[-1])
        for value in values or []:
            self.add_row(value)

    def validate(self) -> tuple[bool, str]:
        if not self.rows:
            return False, "自定义字段模式至少需要添加一个字段"
        names = []
        for row in self.rows:
            name = row.name_edit.text().strip()
            if not name:
                return False, "字段名不能为空"
            if name in names:
                return False, f"字段名不能重复：{name}"
            names.append(name)
            try:
                row.get_value()
            except ValueError as error:
                return False, str(error)
        return True, ""


class DataMockForm(QtWidgets.QWidget):
    """data.mock 表单：自定义字段、模板，或导入并编辑 SQL/Excel 表结构。"""
    previewFinished = QtCore.Signal(object)

    def __init__(
        self,
        schema: dict[str, Any],
        runtime: Runtime | None = None,
        parent=None,
        *,
        runtime_root: Path | None = None,
    ):
        super().__init__(parent)
        self.schema = schema or {}
        self.runtime = runtime
        self.runtime_root = runtime_root
        self.imported_tables: list[dict[str, Any]] = []
        self.imported_table_fields: dict[str, list[dict[str, Any]]] = {}
        self.selected_import_table = ""
        self._preview_running = False
        self._preview_worker: RuntimeWorker | None = None
        self._init_ui()

    def _label(self, text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        basic = QtWidgets.QGroupBox("生成设置")
        form = QtWidgets.QFormLayout(basic)
        form.setSpacing(10)
        self.count_spin = QtWidgets.QSpinBox()
        self.count_spin.setRange(1, 100000)
        self.count_spin.setValue(10)
        self.format_combo = QtWidgets.QComboBox()
        for label, key in [("JSON", "json"), ("CSV", "csv"), ("Excel", "xlsx"), ("TXT", "txt"), ("SQL", "sql"), ("ZIP 数据包", "zip")]:
            self.format_combo.addItem(label, key)
        self.seed_edit = QtWidgets.QLineEdit()
        self.seed_edit.setPlaceholderText("留空则每次随机")
        self.table_edit = QtWidgets.QLineEdit()
        self.table_edit.setPlaceholderText("可选，例如 user_info")
        form.addRow(self._label("生成条数"), self.count_spin)
        form.addRow(self._label("输出格式"), self.format_combo)
        form.addRow(self._label("随机种子"), self.seed_edit)
        form.addRow(self._label("输出表名"), self.table_edit)
        layout.addWidget(basic)

        mode_box = QtWidgets.QGroupBox("表结构来源")
        mode_form = QtWidgets.QFormLayout(mode_box)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("自定义字段（默认）", "fields")
        self.mode_combo.addItem("快捷模板", "template")
        self.mode_combo.addItem("导入 SQL / Excel 表结构", "source")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_form.addRow(self._label("配置模式"), self.mode_combo)
        layout.addWidget(mode_box)

        self.stack = QtWidgets.QStackedWidget()
        custom_page = QtWidgets.QWidget()
        custom_layout = QtWidgets.QVBoxLayout(custom_page)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self.fields_editor = DataMockFieldsEditor()
        custom_layout.addWidget(self.fields_editor)
        self.stack.addWidget(custom_page)

        template_page = QtWidgets.QWidget()
        template_form = QtWidgets.QFormLayout(template_page)
        self.template_combo = QtWidgets.QComboBox()
        for label, key in [("客户数据", "retail_customer"), ("账户数据", "account"), ("商品数据", "product"), ("交易数据", "transaction")]:
            self.template_combo.addItem(label, key)
        template_form.addRow(self._label("选择模板"), self.template_combo)
        self.stack.addWidget(template_page)

        source_page = QtWidgets.QWidget()
        source_layout = QtWidgets.QVBoxLayout(source_page)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_form = QtWidgets.QFormLayout()
        self.source_format_combo = QtWidgets.QComboBox()
        self.source_format_combo.addItem("SQL DDL（支持多表）", "sql")
        self.source_format_combo.addItem("Excel 字段清单", "excel")
        self.source_picker = SingleFilePicker("选择 SQL 或 Excel 字段清单…", "SQL / Excel 文件 (*.sql *.ddl *.xlsx);;所有文件 (*.*)")
        self.source_picker.valueChanged.connect(self._on_source_path_changed)
        self.source_format_combo.currentIndexChanged.connect(self._on_source_format_changed)
        source_form.addRow(self._label("导入类型"), self.source_format_combo)
        source_form.addRow(self._label("结构文件"), self.source_picker)
        source_layout.addLayout(source_form)
        action_row = QtWidgets.QHBoxLayout()
        self.preview_btn = QtWidgets.QPushButton("解析并展示表结构")
        self.preview_btn.setObjectName("secondaryButton")
        self.preview_btn.clicked.connect(self._preview_source)
        action_row.addWidget(self.preview_btn)
        self.preview_status = QtWidgets.QLabel("导入后可查看表名、字段名、类型，并逐字段调整生成规则")
        self.preview_status.setObjectName("mutedText")
        action_row.addWidget(self.preview_status, 1)
        source_layout.addLayout(action_row)
        table_row = QtWidgets.QHBoxLayout()
        table_row.addWidget(self._label("选择导入表"))
        self.import_table_combo = QtWidgets.QComboBox()
        self.import_table_combo.setEnabled(False)
        self.import_table_combo.currentIndexChanged.connect(self._on_import_table_changed)
        table_row.addWidget(self.import_table_combo, 1)
        source_layout.addLayout(table_row)
        self.import_fields_editor = DataMockFieldsEditor()
        self.import_fields_editor.setEnabled(False)
        source_layout.addWidget(self.import_fields_editor)
        self.stack.addWidget(source_page)
        layout.addWidget(self.stack)

        output_box = QtWidgets.QGroupBox("输出高级设置")
        output_form = QtWidgets.QFormLayout(output_box)
        self.sql_dialect_combo = QtWidgets.QComboBox()
        for label, key in [("MySQL", "mysql"), ("PostgreSQL", "postgresql"), ("SQL Server", "sqlserver"), ("Oracle", "oracle"), ("SQLite", "sqlite")]:
            self.sql_dialect_combo.addItem(label, key)
        self.sql_table_edit = QtWidgets.QLineEdit()
        self.sql_table_edit.setPlaceholderText("留空使用输出表名")
        self.sql_create_check = QtWidgets.QCheckBox("生成 CREATE TABLE")
        self.sql_transaction_check = QtWidgets.QCheckBox("使用事务")
        self.sql_transaction_check.setChecked(True)
        self.sql_batch_spin = QtWidgets.QSpinBox()
        self.sql_batch_spin.setRange(1, 1000)
        self.sql_batch_spin.setValue(500)
        self.txt_delimiter_edit = QtWidgets.QLineEdit("|")
        self.txt_delimiter_edit.setMaxLength(1)
        self.txt_header_check = QtWidgets.QCheckBox("TXT 包含表头")
        self.txt_header_check.setChecked(True)
        output_form.addRow(self._label("SQL 方言"), self.sql_dialect_combo)
        output_form.addRow(self._label("SQL 表名"), self.sql_table_edit)
        output_form.addRow(self._label("SQL 批量大小"), self.sql_batch_spin)
        output_form.addRow(self._label("SQL 选项"), self.sql_create_check)
        output_form.addRow("", self.sql_transaction_check)
        output_form.addRow(self._label("TXT 分隔符"), self.txt_delimiter_edit)
        output_form.addRow("", self.txt_header_check)
        layout.addWidget(output_box)
        layout.addStretch()
        self.stack.setCurrentIndex(0)

    def _on_mode_changed(self, index: int):
        self.stack.setCurrentIndex(index)
        if self.mode_combo.currentData() == "source" and self.imported_tables:
            self._sync_import_editor()

    def _on_source_path_changed(self, path: str):
        if self._preview_running:
            return
        self._clear_imported_tables()
        if path and self.mode_combo.currentData() == "source":
            QtCore.QTimer.singleShot(0, self._preview_source)

    def _on_source_format_changed(self, _index: int):
        if self._preview_running:
            return
        self._clear_imported_tables()
        path = self.source_picker.get_path() if hasattr(self, "source_picker") else ""
        if path and self.mode_combo.currentData() == "source":
            QtCore.QTimer.singleShot(0, self._preview_source)

    def _clear_imported_tables(self):
        if not hasattr(self, "imported_table_fields") or not self.imported_table_fields:
            return
        self.imported_tables = []
        self.imported_table_fields = {}
        self.selected_import_table = ""
        self.import_table_combo.clear()
        self.import_table_combo.setEnabled(False)
        self.import_fields_editor.set_values([])
        self.import_fields_editor.setEnabled(False)
        self.preview_status.setText("文件或格式已变化，请重新解析表结构")

    def _set_preview_busy(self, busy: bool):
        self._preview_running = busy
        self.preview_btn.setEnabled(not busy)
        self.source_picker.setEnabled(not busy)
        self.source_format_combo.setEnabled(not busy)

    def _preview_source(self):
        if self._preview_running:
            return
        path = self.source_picker.get_path()
        if not path:
            QtWidgets.QMessageBox.warning(self, "无法解析", "请先选择 SQL 或 Excel 表结构文件")
            return
        if self.runtime is None:
            QtWidgets.QMessageBox.critical(self, "无法解析", "当前界面未连接 Runtime")
            return
        self._set_preview_busy(True)
        self.preview_status.setText("正在解析表结构…")
        params = {"count": 1, "format": "json", "source_file": path, "source_format": self.source_format_combo.currentData(), "preview": True}
        worker_root = self.runtime_root
        if worker_root is None and not getattr(sys, "frozen", False):
            worker_root = self.runtime.root
        worker = RuntimeWorker(worker_root, "data.mock", params)
        worker.signals.finished.connect(self._on_preview_finished)
        worker.signals.failed.connect(self._on_preview_failed)
        self._preview_worker = worker
        QtCore.QThreadPool.globalInstance().start(worker)

    def _on_preview_finished(self, _task_id: str, result: Any, _elapsed: float):
        self._preview_worker = None
        self._set_preview_busy(False)
        if result.status != "success":
            self.preview_status.setText("解析失败")
            QtWidgets.QMessageBox.critical(self, "表结构解析失败", result.message)
            return
        self._set_imported_tables(result.data.get("tables") or [])

    def _on_preview_failed(self, _command: str, error: str, _elapsed: float):
        self._preview_worker = None
        self._set_preview_busy(False)
        self.preview_status.setText("解析失败")
        QtWidgets.QMessageBox.critical(self, "表结构解析失败", error)

    def _set_imported_tables(self, tables: list[dict[str, Any]]):
        valid = [item for item in tables if item.get("name") and isinstance(item.get("fields"), list)]
        if not valid:
            self.preview_status.setText("未解析到表结构")
            QtWidgets.QMessageBox.warning(self, "表结构为空", "文件中没有可编辑的表或字段")
            return
        self.imported_tables = valid
        self.imported_table_fields = {str(item["name"]): list(item["fields"]) for item in valid}
        self.import_table_combo.blockSignals(True)
        self.import_table_combo.clear()
        for item in valid:
            self.import_table_combo.addItem(f"{item['name']}（{len(item['fields'])} 个字段）", item["name"])
        self.import_table_combo.blockSignals(False)
        self.import_table_combo.setEnabled(True)
        self.import_fields_editor.setEnabled(True)
        self.selected_import_table = str(valid[0]["name"])
        self._sync_import_editor()
        self.preview_status.setText(f"已解析 {len(valid)} 张表，可选择表并修改字段规则")

    def _save_import_editor(self):
        if not self.selected_import_table or not self.import_fields_editor.isEnabled():
            return
        try:
            self.imported_table_fields[self.selected_import_table] = self.import_fields_editor.get_values()
        except ValueError:
            pass

    def _on_import_table_changed(self, index: int):
        self._save_import_editor()
        if index < 0:
            return
        self.selected_import_table = str(self.import_table_combo.itemData(index))
        self._sync_import_editor()

    def _sync_import_editor(self):
        fields = self.imported_table_fields.get(self.selected_import_table, [])
        self.import_fields_editor.set_values(fields)
        self.table_edit.setText(self.selected_import_table)
        self.sql_table_edit.setPlaceholderText(f"留空使用 {self.selected_import_table}")

    def get_values(self) -> dict[str, Any]:
        params = {"count": self.count_spin.value(), "format": self.format_combo.currentData()}
        if self.seed_edit.text().strip():
            try:
                params["seed"] = int(self.seed_edit.text().strip())
            except ValueError as error:
                raise ValueError("随机种子必须是整数") from error
        if self.table_edit.text().strip():
            params["table"] = self.table_edit.text().strip()
        mode = self.mode_combo.currentData()
        if mode == "fields":
            params["fields"] = self.fields_editor.get_values()
        elif mode == "template":
            params["template"] = self.template_combo.currentData()
        else:
            self._save_import_editor()
            imported_fields = self.imported_table_fields.get(self.selected_import_table) or []
            if imported_fields:
                # 解析后提交当前表的编辑结果，避免 source_file 与 fields 同时触发模式互斥校验。
                params["fields"] = imported_fields
                params["source_table"] = self.selected_import_table
            else:
                params["source_file"] = self.source_picker.get_path()
                params["source_format"] = self.source_format_combo.currentData()
        params.update({"sql_dialect": self.sql_dialect_combo.currentData(), "sql_batch_size": self.sql_batch_spin.value(), "sql_transaction": self.sql_transaction_check.isChecked(), "sql_create_table": self.sql_create_check.isChecked(), "txt_delimiter": self.txt_delimiter_edit.text() or "|", "txt_header": self.txt_header_check.isChecked()})
        if self.sql_table_edit.text().strip():
            params["sql_table"] = self.sql_table_edit.text().strip()
        return params

    def set_values(self, values: dict[str, Any]):
        if "count" in values: self.count_spin.setValue(int(values["count"]))
        if "format" in values: self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(values["format"])))
        if "seed" in values: self.seed_edit.setText(str(values["seed"]))
        if "table" in values: self.table_edit.setText(str(values["table"]))
        if values.get("fields") is not None:
            self.mode_combo.setCurrentIndex(0); self.fields_editor.set_values(values.get("fields") or [])
        elif values.get("template"):
            self.mode_combo.setCurrentIndex(1); self.template_combo.setCurrentIndex(max(0, self.template_combo.findData(values["template"])))
        elif values.get("source_file"):
            self.mode_combo.setCurrentIndex(2); self.source_picker.set_path(str(values["source_file"])); self.source_format_combo.setCurrentIndex(max(0, self.source_format_combo.findData(values.get("source_format", "sql"))))
        if "sql_dialect" in values: self.sql_dialect_combo.setCurrentIndex(max(0, self.sql_dialect_combo.findData(values["sql_dialect"])))
        if "sql_table" in values: self.sql_table_edit.setText(str(values["sql_table"]))
        if "sql_batch_size" in values: self.sql_batch_spin.setValue(int(values["sql_batch_size"]))
        if "sql_transaction" in values: self.sql_transaction_check.setChecked(bool(values["sql_transaction"]))
        if "sql_create_table" in values: self.sql_create_check.setChecked(bool(values["sql_create_table"]))
        if "txt_delimiter" in values: self.txt_delimiter_edit.setText(str(values["txt_delimiter"]))
        if "txt_header" in values: self.txt_header_check.setChecked(bool(values["txt_header"]))

    def validate_locally(self) -> tuple[bool, str]:
        try:
            values = self.get_values()
        except ValueError as error:
            return False, str(error)
        if values["format"] == "txt" and len(values.get("txt_delimiter", "")) != 1:
            return False, "TXT 分隔符必须是一个字符"
        mode = self.mode_combo.currentData()
        if mode == "fields":
            return self.fields_editor.validate()
        if mode == "source":
            if not self.source_picker.get_path().strip():
                return False, "请选择要导入的 SQL 或 Excel 表结构文件"
            if not self.imported_table_fields.get(self.selected_import_table):
                return False, "请先点击“解析并展示表结构”，并确认导入表包含字段"
            return self.import_fields_editor.validate()
        return True, ""

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
        self.advanced_keys: set[str] = set()
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
            if key in required_keys or key in ("count", "format", "input", "dialect", "template", "seed", "rules", "interactive"):
                basic_props[key] = spec
            else:
                advanced_props[key] = spec

        # 1. 必填与主要参数组
        if basic_props:
            basic_box = QtWidgets.QGroupBox("主要参数设置")
            basic_layout = QtWidgets.QVBoxLayout(basic_box)
            basic_layout.setSpacing(12)
            basic_layout.setContentsMargins(14, 16, 14, 14)

            for key, spec in basic_props.items():
                w = self._create_field_widget(key, spec, is_required=(key in required_keys))
                basic_layout.addWidget(w)
            main_layout.addWidget(basic_box)

        # 2. 高级参数折叠面板
        if advanced_props:
            self.advanced_keys = set(advanced_props)
            adv_container = QtWidgets.QWidget()
            adv_container.setObjectName("cardPanel")
            adv_layout = QtWidgets.QVBoxLayout(adv_container)
            adv_layout.setContentsMargins(14, 12, 14, 14)
            adv_layout.setSpacing(10)

            # 折叠切换按钮
            self.adv_toggle_btn = QtWidgets.QPushButton("▶ 展开高级参数配置")
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
            self.adv_toggle_btn.setText("▼ 收起高级参数配置")
        else:
            self.adv_toggle_btn.setText("▶ 展开高级参数配置")

    def _create_field_widget(self, key: str, spec: dict[str, Any], is_required: bool) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 头部标签栏
        label_bar = QtWidgets.QHBoxLayout()
        label_bar.setSpacing(6)

        title_text = f"<b>{_parameter_label(key)}</b> <span style='color: #94a3b8;'>（{key}）</span>"
        if is_required:
            title_text += " <span style='color: #ef4444; font-weight: bold;'>*必填</span>"
        lbl = QtWidgets.QLabel(title_text)
        label_bar.addWidget(lbl)

        # 类型或格式标记
        type_str = spec.get("type", "string")
        fmt = spec.get("format")
        badge_text = TYPE_LABELS.get(type_str, type_str) + (f" / {type_str}" if type_str in TYPE_LABELS else "") + (f": {fmt}" if fmt else "")
        badge = QtWidgets.QLabel(f"[{badge_text}]")
        badge.setObjectName("tagLabelMuted")
        label_bar.addWidget(badge)

        label_bar.addStretch()
        layout.addLayout(label_bar)

        # 描述单独占一行并允许换行，避免长描述把表单的最小宽度撑出窗口。
        desc = _parameter_description(key, spec)
        if desc:
            desc_lbl = QtWidgets.QLabel(f"— {desc}")
            desc_lbl.setObjectName("mutedText")
            desc_lbl.setWordWrap(True)
            desc_lbl.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Preferred,
            )
            layout.addWidget(desc_lbl)

        # 表单输入控件
        ctrl = None
        enum_values = spec.get("enum")
        default_val = spec.get("default")

        if fmt == "file-path":
            filter_str = "所有文件 (*.*)"
            if "sql" in key or "sql" in self.command_name:
                filter_str = "SQL 文件 (*.sql *.ddl);;所有文件 (*.*)"
            elif "excel" in key or "input" in key:
                filter_str = "Excel / SQL / 文本 (*.xlsx *.xlsm *.sql *.csv *.json);;所有文件 (*.*)"
            ctrl = SingleFilePicker(placeholder=f"请选择 {_parameter_label(key)}（{key}）文件...", filter_str=filter_str)
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
                combo.addItem(_enum_label(key, item), item)
            if default_val is not None and default_val in enum_values:
                combo.setCurrentText(str(default_val))
            self.fields[key] = ("enum", combo)
            layout.addWidget(combo)

        elif type_str == "boolean":
            chk = QtWidgets.QCheckBox("启用（True）")
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
                ctrl = MultiFilesPicker(filter_str="图片 / 报告文件 (*.png *.jpg *.jpeg *.docx *.xlsx);;所有文件 (*.*)")
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
            edit.setPlaceholderText(f"请输入 {_parameter_label(key)}（{key}）...")
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
                    if txt.startswith("["):
                        try:
                            parsed = json.loads(txt)
                        except json.JSONDecodeError as error:
                            raise FormInputError(key, "请输入有效的 JSON 数组") from error
                        if not isinstance(parsed, list):
                            raise FormInputError(key, "参数必须是 JSON 数组")
                        params[key] = parsed
                    else:
                        params[key] = [x.strip() for x in txt.split(",") if x.strip()]
            elif ftype == "object-text":
                txt = widget.text().strip()
                if txt:
                    try:
                        parsed = json.loads(txt)
                    except json.JSONDecodeError as error:
                        raise FormInputError(key, "请输入有效的 JSON 对象") from error
                    if not isinstance(parsed, dict):
                        raise FormInputError(key, "参数必须是 JSON 对象")
                    params[key] = parsed
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

    @staticmethod
    def _root_field(field: str | None) -> str:
        if not field:
            return ""
        return field.split("[", 1)[0].split(".", 1)[0]

    def set_field_error(self, key: str, message: str) -> bool:
        root_key = self._root_field(key)
        if root_key not in self.error_labels:
            return False
        lbl = self.error_labels[root_key]
        lbl.setText(f"❌ {message}")
        lbl.setVisible(True)
        if root_key in self.advanced_keys and hasattr(self, "adv_toggle_btn"):
            self.adv_toggle_btn.setChecked(True)
        _, widget = self.fields[root_key]
        focus_target = getattr(widget, "line_edit", widget)
        if hasattr(focus_target, "setFocus"):
            focus_target.setFocus()
        return True

    def validate_locally(self) -> tuple[bool, str]:
        """Validate obvious input errors; Runtime remains the Schema authority."""
        self.clear_errors()
        try:
            values = self.get_values()
        except FormInputError as error:
            self.set_field_error(error.field, str(error))
            return False, str(error)

        required_keys: list[str] = self.schema.get("required", [])
        for required in required_keys:
            if required not in values or values[required] is None or values[required] == "" or values[required] == []:
                message = "此项为必填字段，请输入或选择有效值"
                self.set_field_error(required, message)
                return False, f"必填字段 [{required}] 不能为空"

        for key, (field_type, widget) in self.fields.items():
            paths: list[str] = []
            if field_type == "file-path":
                value = widget.get_path().strip()
                if value:
                    paths = [value]
            elif field_type == "array-files":
                paths = [str(value).strip() for value in widget.get_paths() if str(value).strip()]
            for value in paths:
                path = Path(value).expanduser()
                if not path.exists():
                    message = f"文件不存在：{value}"
                    self.set_field_error(key, message)
                    return False, message
                if not path.is_file():
                    message = f"不是有效文件：{value}"
                    self.set_field_error(key, message)
                    return False, message
                if not os.access(path, os.R_OK):
                    message = f"文件不可读：{value}"
                    self.set_field_error(key, message)
                    return False, message

        return True, ""


# ==============================================================================
# 视图 1：工具目录
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

        plugin_tag = QtWidgets.QLabel(f"{PLUGIN_LABELS.get(manifest.name, manifest.name)}（{manifest.name}） v{manifest.version}")
        plugin_tag.setObjectName("tagLabel")

        top_bar.addWidget(name_lbl)
        top_bar.addWidget(plugin_tag)
        top_bar.addStretch()

        # 分类与状态徽标
        cat_tag = QtWidgets.QLabel(f"{CATEGORY_LABELS.get(manifest.category, manifest.category)}（{manifest.category.upper()}）")
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

        action_lbl = QtWidgets.QLabel("配置此命令 →")
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
        self.all_cards: list[tuple[str, str, str, str, str, ToolCardWidget]] = []
        self._catalog_state = "loading"
        self._init_ui()

    def _init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # 页面标题
        header_box = QtWidgets.QHBoxLayout()
        title_box = QtWidgets.QVBoxLayout()
        title_lbl = QtWidgets.QLabel("工具目录")
        title_lbl.setObjectName("pageTitle")
        subtitle_lbl = QtWidgets.QLabel("浏览并运行 TestBox 已发现的测试效能工具插件与命令")
        subtitle_lbl.setObjectName("mutedText")
        title_box.addWidget(title_lbl)
        title_box.addWidget(subtitle_lbl)

        header_box.addLayout(title_box)
        header_box.addStretch()

        self.refresh_btn = QtWidgets.QPushButton("🔄 刷新插件目录")
        self.refresh_btn.setObjectName("secondaryButton")
        self.refresh_btn.clicked.connect(self.reload_tools)
        header_box.addWidget(self.refresh_btn)

        layout.addLayout(header_box)

        # 搜索与分类过滤条
        filter_bar = QtWidgets.QHBoxLayout()
        filter_bar.setSpacing(12)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索命令、插件名称、描述或分类关键字...")
        self.search_input.textChanged.connect(self._filter_cards)
        filter_bar.addWidget(self.search_input, 2)

        self.category_combo = QtWidgets.QComboBox()
        self.category_combo.addItem("全部分类", "all")
        self.category_combo.currentIndexChanged.connect(self._filter_cards)
        filter_bar.addWidget(self.category_combo, 1)

        layout.addLayout(filter_bar)

        self.unavailable_label = QtWidgets.QLabel("")
        self.unavailable_label.setObjectName("mutedText")
        self.unavailable_label.setWordWrap(True)
        self.unavailable_label.setVisible(False)
        layout.addWidget(self.unavailable_label)

        # 滚动区域放置卡片
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        self.cards_container = QtWidgets.QWidget()
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)

        # 统一的加载、空和错误状态，不改变现有目录滚动区域和卡片层级。
        self.state_widget = QtWidgets.QWidget()
        state_layout = QtWidgets.QVBoxLayout(self.state_widget)
        state_layout.setContentsMargins(40, 60, 40, 60)
        state_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.state_icon = QtWidgets.QLabel("")
        self.state_icon.setStyleSheet("font-size: 48px;")
        self.state_icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.state_text = QtWidgets.QLabel("")
        self.state_text.setStyleSheet("color: #64748b; font-size: 15px; font-weight: 500;")
        self.state_text.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.state_detail = QtWidgets.QLabel("")
        self.state_detail.setObjectName("mutedText")
        self.state_detail.setWordWrap(True)
        self.state_detail.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.state_retry_btn = QtWidgets.QPushButton("重试")
        self.state_retry_btn.setObjectName("secondaryButton")
        self.state_retry_btn.clicked.connect(self.reload_tools)
        state_layout.addWidget(self.state_icon)
        state_layout.addWidget(self.state_text)
        state_layout.addWidget(self.state_detail)
        state_layout.addWidget(self.state_retry_btn, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        self.cards_layout.addWidget(self.state_widget)
        # 兼容现有页面/测试对空状态控件的命名；实际由统一状态组件承载。
        self.empty_widget = self.state_widget

        scroll.setWidget(self.cards_container)
        layout.addWidget(scroll, 1)

        self.reload_tools()

    def _set_catalog_state(self, state: str, detail: str = ""):
        self._catalog_state = state
        states = {
            "loading": ("⏳", "正在加载工具目录", "正在从 Runtime 读取插件和命令信息...", False),
            "empty_plugins": ("📦", "暂无可用工具插件", "请检查插件目录或打开“插件与诊断”查看不可用原因。", False),
            "empty_search": ("🔎", "未找到匹配的工具命令", "请清除搜索词或切换分类后重试。", False),
            "error": ("⚠️", "工具目录加载失败", detail or "Runtime 未能读取插件和命令信息。", True),
        }
        icon, text, default_detail, can_retry = states.get(state, states["error"])
        self.state_icon.setText(icon)
        self.state_text.setText(text)
        self.state_detail.setText(detail or default_detail)
        self.state_retry_btn.setVisible(can_retry)
        self.state_widget.setVisible(True)
        for _, _, _, _, _, card in self.all_cards:
            card.setVisible(False)

    def _clear_cards(self):
        for _, _, _, _, _, card in self.all_cards:
            self.cards_layout.removeWidget(card)
            card.deleteLater()
        self.all_cards.clear()

    def _unavailable_plugins(self) -> list[dict[str, str]]:
        getter = getattr(self.runtime, "list_unavailable_plugins", None)
        if callable(getter):
            return getter()
        manager = getattr(self.runtime, "manager", None)
        unavailable = getattr(manager, "unavailable", {})
        return [{"path": path, "reason": reason, "status": "unavailable"} for path, reason in sorted(unavailable.items())]

    def _show_unavailable(self, items: list[dict[str, str]]):
        if not items:
            self.unavailable_label.clear()
            self.unavailable_label.setVisible(False)
            return
        preview = "；".join(f"{item.get('path', '插件')}：{item.get('reason', '未知原因')}" for item in items[:3])
        suffix = f"（另有 {len(items) - 3} 项未展开）" if len(items) > 3 else ""
        self.unavailable_label.setText(f"⚠️ {len(items)} 个插件当前不可用：{preview}{suffix}")
        self.unavailable_label.setVisible(True)

    def reload_tools(self):
        self._clear_cards()
        self._set_catalog_state("loading")
        self.refresh_btn.setEnabled(False)
        self.search_input.setEnabled(False)
        self.category_combo.setEnabled(False)
        try:
            commands_map = self.runtime.list_commands()
            unavailable = self._unavailable_plugins()
        except Exception as error:
            self._show_unavailable([])
            self._set_catalog_state("error", f"调用 Runtime.list_commands 失败：{error}")
            self.refresh_btn.setEnabled(True)
            self.search_input.setEnabled(True)
            self.category_combo.setEnabled(True)
            return

        categories = set()
        for cmd_name, manifest in sorted(commands_map.items()):
            cmd_desc = next((cmd.description for cmd in manifest.commands if cmd.name == cmd_name), "")
            card = ToolCardWidget(cmd_name, cmd_desc, manifest)
            card.clicked.connect(self.commandSelected.emit)
            self.cards_layout.addWidget(card)
            search_text = " ".join((cmd_name, manifest.name, manifest.description, cmd_desc, manifest.category)).lower()
            self.all_cards.append((cmd_name, manifest.name, manifest.category, cmd_desc, search_text, card))
            categories.add(manifest.category)

        current_cat = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("全部分类", "all")
        for cat in sorted(categories):
            self.category_combo.addItem(f"分类：{CATEGORY_LABELS.get(cat, cat)}（{cat.upper()}）", cat)
        if current_cat:
            idx = self.category_combo.findData(current_cat)
            if idx >= 0:
                self.category_combo.setCurrentIndex(idx)
        self.category_combo.blockSignals(False)
        self._show_unavailable(unavailable)
        self.refresh_btn.setEnabled(True)
        self.search_input.setEnabled(True)
        self.category_combo.setEnabled(True)
        self._filter_cards()

    def _filter_cards(self):
        if self._catalog_state in ("loading", "error"):
            return
        query = self.search_input.text().strip().lower()
        selected_cat = self.category_combo.currentData() or "all"

        visible_count = 0
        for cmd_name, plugin_name, category, description, search_text, card in self.all_cards:
            match_query = (
                (not query)
                or (query in cmd_name.lower())
                or (query in plugin_name.lower())
                or (query in description.lower())
                or (query in category.lower())
                or (query in search_text)
            )
            match_cat = (selected_cat == "all") or (category == selected_cat)
            show = match_query and match_cat
            card.setVisible(show)
            if show:
                visible_count += 1

        if visible_count == 0:
            self.empty_widget.setVisible(visible_count == 0)
            self._set_catalog_state("empty_search" if self.all_cards else "empty_plugins")
        else:
            self._catalog_state = "ready"
            self.state_widget.setVisible(False)


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

    def __init__(self, runtime: Runtime, parent=None, *, runtime_root: Path | None = None):
        super().__init__(parent)
        self.runtime = runtime
        self.runtime_root = runtime_root
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

        # 主体左右分栏。使用可伸缩布局，不固定右侧面板宽度，避免窗口变窄时
        # 左右两栏的总宽度超过可用区域而导致右侧内容被裁切。
        split_layout = QtWidgets.QHBoxLayout()
        split_layout.setSpacing(18)

        # === 左栏：Schema 表单滚动区 ===
        form_panel = QtWidgets.QWidget()
        self.form_panel = form_panel
        form_layout = QtWidgets.QVBoxLayout(form_panel)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(12)

        self.form_scroll = QtWidgets.QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        self.form_container = QtWidgets.QWidget()
        self.form_container_layout = QtWidgets.QVBoxLayout(self.form_container)
        self.form_container_layout.setContentsMargins(0, 0, 0, 0)
        self.form_scroll.setWidget(self.form_container)

        form_layout.addWidget(self.form_scroll, 1)

        self.form_feedback_label = QtWidgets.QLabel("")
        self.form_feedback_label.setObjectName("fieldErrorLabel")
        self.form_feedback_label.setWordWrap(True)
        self.form_feedback_label.setVisible(False)
        form_layout.addWidget(self.form_feedback_label)

        # 底部执行与操作栏
        action_bar = QtWidgets.QHBoxLayout()
        action_bar.setSpacing(12)

        self.reset_btn = QtWidgets.QPushButton("重置表单")
        self.reset_btn.setObjectName("secondaryButton")
        self.reset_btn.clicked.connect(self._reset_form)
        action_bar.addWidget(self.reset_btn)

        action_bar.addStretch()

        self.submit_btn = QtWidgets.QPushButton("🚀 立即执行任务")
        self.submit_btn.setObjectName("primaryButton")
        self.submit_btn.setFixedHeight(36)
        self.submit_btn.setStyleSheet("font-size: 14px; font-weight: 700; padding: 0 24px;")
        self.submit_btn.clicked.connect(self._on_submit)
        action_bar.addWidget(self.submit_btn)

        form_layout.addLayout(action_bar)
        split_layout.addWidget(form_panel, 7)

        # === 右栏：命令元数据与帮助侧栏 ===
        info_panel = QtWidgets.QWidget()
        self.info_panel = info_panel
        info_panel.setObjectName("cardPanel")
        info_panel.setMinimumWidth(0)
        info_panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
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
        self._split_layout = split_layout

    def _set_form_feedback(self, message: str = ""):
        self.form_feedback_label.setText(f"❌ {message}" if message else "")
        self.form_feedback_label.setVisible(bool(message))

    def load_command(self, command_name: str, preset_params: dict | None = None):
        self.current_command = command_name
        self._set_form_feedback()
        self.submit_btn.setEnabled(False)
        if self.form_widget:
            self.form_container_layout.removeWidget(self.form_widget)
            self.form_widget.deleteLater()
            self.form_widget = None
        try:
            self.current_manifest = self.runtime.get_command(command_name)
            self.current_schema = self.runtime.get_command_schema(command_name)
        except Exception as error:
            self.current_manifest = None
            self.current_schema = {}
            self.cmd_title_lbl.setText(f"命令: {command_name}")
            self.plugin_badge.setText("不可用")
            self._set_form_feedback(f"无法从 Runtime 加载命令 Schema：{error}")
            return

        self.cmd_title_lbl.setText(f"命令: {command_name}")
        self.plugin_badge.setText(f"{PLUGIN_LABELS.get(self.current_manifest.name, self.current_manifest.name)}（{self.current_manifest.name}） v{self.current_manifest.version}")
        self.lbl_manifest_ver.setText(self.current_manifest.version)
        self.lbl_category.setText(f"{CATEGORY_LABELS.get(self.current_manifest.category, self.current_manifest.category)}（{self.current_manifest.category.upper()}）")
        caps = self.current_manifest.capabilities or {}
        self.lbl_concurrency.setText("支持并发" if caps.get("concurrency", True) else "仅串行执行")
        self.lbl_filesystem.setText({"output-only": "仅输出目录（output-only）", "none": "不写入文件（none）"}.get(caps.get("filesystem", "output-only"), str(caps.get("filesystem", "output-only"))))
        self.lbl_compat.setText(self.current_manifest.core_compatibility or "*")
        cmd_desc = next((cmd.description for cmd in self.current_manifest.commands if cmd.name == command_name), "")
        self.info_desc_lbl.setText(cmd_desc or self.current_manifest.description)

        self.form_widget = DataMockForm(self.current_schema, runtime=self.runtime, runtime_root=self.runtime_root) if command_name == "data.mock" else DynamicSchemaForm(self.current_schema, command_name=command_name)
        if preset_params:
            self.form_widget.set_values(preset_params)
        self.form_container_layout.addWidget(self.form_widget)
        self.submit_btn.setEnabled(True)

    def _reset_form(self):
        if self.current_command:
            self.load_command(self.current_command)

    def _on_submit(self):
        if not self.form_widget:
            return
        self._set_form_feedback()
        valid, error_message = self.form_widget.validate_locally()
        if not valid:
            self._set_form_feedback(error_message or "请检查标记的输入参数。")
            return
        try:
            params = self.form_widget.get_values()
            params = self.runtime.validate_params(self.current_command, params)
        except FormInputError as error:
            if hasattr(self.form_widget, "set_field_error"):
                self.form_widget.set_field_error(error.field, str(error))
            self._set_form_feedback(str(error))
            return
        except SchemaValidationError as error:
            handled = False
            if hasattr(self.form_widget, "set_field_error"):
                handled = bool(self.form_widget.set_field_error(error.field or "", str(error)))
            self._set_form_feedback(str(error) if not handled else "请修正标记的字段后重新提交。")
            return
        except Exception as error:
            self._set_form_feedback(f"Runtime 参数校验失败：{error}")
            return
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
        title_lbl = QtWidgets.QLabel("执行工作区")
        title_lbl.setObjectName("pageTitle")
        self.sub_title_lbl = QtWidgets.QLabel("正在调度插件主进程执行任务…")
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
        self.status_title_lbl = QtWidgets.QLabel("任务正在运行中")
        self.status_title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #2563eb;")
        self.status_desc_lbl = QtWidgets.QLabel("任务处于等待执行或运行中（PENDING / RUNNING），正在等待插件返回。")
        self.status_desc_lbl.setObjectName("mutedText")
        self.status_desc_lbl.setWordWrap(True)
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
        params_group = QtWidgets.QGroupBox("提交参数摘要")
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
        self.status_title_lbl.setText("等待 Runtime 与插件返回")
        self.status_desc_lbl.setText(
            "任务处于等待执行或运行中（PENDING / RUNNING）。当前 Host 协议不会提供实时百分比或日志流。"
        )
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
    支持 PENDING、RUNNING、SUCCEEDED、FAILED、CANCELLED、ABANDONED 六种 Runtime 状态。
    Warning、Empty、Missing result/output 和 Permission denied 仅作为展示层 facet，
    不扩展 Runtime 状态机。
    """
    reExecuteRequested = QtCore.Signal(str, dict)  # command_name, params
    sqlSelectRequested = QtCore.Signal(str, dict)  # command_name, prefilled params
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

        self.btn_re_execute = QtWidgets.QPushButton("🔄 修改参数后重新执行")
        self.btn_re_execute.setObjectName("secondaryButton")
        self.btn_re_execute.clicked.connect(self._on_re_execute)
        top_bar.addWidget(self.btn_re_execute)

        # SQL 字段清单的下游衔接只回填 sql.select 表单，不会创建或执行新任务。
        self.btn_open_sql_select = QtWidgets.QPushButton("→ 用字段清单生成 SELECT")
        self.btn_open_sql_select.setObjectName("secondaryButton")
        self.btn_open_sql_select.setToolTip("将 sql.parse 生成的字段清单预填到 sql.select；仍需由用户确认执行。")
        self.btn_open_sql_select.clicked.connect(self._on_open_sql_select)
        self.btn_open_sql_select.setVisible(False)
        top_bar.addWidget(self.btn_open_sql_select)

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

        self.stats_row = QtWidgets.QWidget()
        stats_row_layout = QtWidgets.QHBoxLayout(self.stats_row)
        stats_row_layout.setContentsMargins(0, 4, 0, 0)
        stats_row_layout.setSpacing(8)

        self.stat_success_badge = QtWidgets.QLabel("")
        self.stat_success_badge.setObjectName("tagLabel")
        stats_row_layout.addWidget(self.stat_success_badge)

        self.stat_failed_badge = QtWidgets.QLabel("")
        self.stat_failed_badge.setObjectName("tagLabelMuted")
        stats_row_layout.addWidget(self.stat_failed_badge)

        self.stat_fields_badge = QtWidgets.QLabel("")
        self.stat_fields_badge.setObjectName("tagLabelInfo")
        stats_row_layout.addWidget(self.stat_fields_badge)

        stats_row_layout.addStretch()
        status_text_layout.addWidget(self.stats_row)
        self.stats_row.setVisible(False)

        banner_layout.addLayout(status_text_layout, 1)

        self.duration_lbl = QtWidgets.QLabel("耗时: -")
        self.duration_lbl.setObjectName("tagLabelMuted")
        banner_layout.addWidget(self.duration_lbl)

        self.content_layout.addWidget(self.status_banner)

        # 2. Runtime 任务元数据与工作区入口
        self.task_meta_box = QtWidgets.QGroupBox("任务与工作区")
        task_meta_layout = QtWidgets.QFormLayout(self.task_meta_box)
        task_meta_layout.setSpacing(8)
        self.meta_task_id_lbl = QtWidgets.QLabel("-")
        self.meta_command_lbl = QtWidgets.QLabel("-")
        self.meta_plugin_lbl = QtWidgets.QLabel("-")
        self.meta_started_lbl = QtWidgets.QLabel("-")
        self.meta_finished_lbl = QtWidgets.QLabel("-")
        self.meta_workspace_lbl = QtWidgets.QLabel("-")
        self.meta_workspace_lbl.setWordWrap(True)
        self.btn_open_workspace = QtWidgets.QPushButton("📂 打开工作区")
        self.btn_open_workspace.setObjectName("smallButton")
        self.btn_open_workspace.clicked.connect(self._open_workspace)

        workspace_row = QtWidgets.QWidget()
        workspace_row_layout = QtWidgets.QHBoxLayout(workspace_row)
        workspace_row_layout.setContentsMargins(0, 0, 0, 0)
        workspace_row_layout.setSpacing(8)
        workspace_row_layout.addWidget(self.meta_workspace_lbl, 1)
        workspace_row_layout.addWidget(self.btn_open_workspace)

        task_meta_layout.addRow("任务 ID:", self.meta_task_id_lbl)
        task_meta_layout.addRow("命令:", self.meta_command_lbl)
        task_meta_layout.addRow("插件:", self.meta_plugin_lbl)
        task_meta_layout.addRow("开始时间:", self.meta_started_lbl)
        task_meta_layout.addRow("完成时间:", self.meta_finished_lbl)
        task_meta_layout.addRow("工作区:", workspace_row)
        self.content_layout.addWidget(self.task_meta_box)

        # 3. 失败/异常诊断区域 (仅失败/中断时显示)
        self.diagnostic_box = QtWidgets.QWidget()
        self.diagnostic_box.setObjectName("diagnosticBox")
        diag_layout = QtWidgets.QVBoxLayout(self.diagnostic_box)
        diag_layout.setContentsMargins(16, 14, 16, 14)
        diag_layout.setSpacing(8)

        diag_title = QtWidgets.QLabel("🚨 错误诊断与处理建议")
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

        # 4. 插件警告与展示层警告，不作为 Runtime 状态。
        self.warnings_box = QtWidgets.QGroupBox("警告 (Warnings)")
        warnings_layout = QtWidgets.QVBoxLayout(self.warnings_box)
        self.warnings_txt = QtWidgets.QPlainTextEdit()
        self.warnings_txt.setReadOnly(True)
        self.warnings_txt.setFixedHeight(100)
        self.warnings_txt.setObjectName("logOutput")
        warnings_layout.addWidget(self.warnings_txt)
        self.warnings_box.setVisible(False)
        self.content_layout.addWidget(self.warnings_box)

        # 5. 输出文件列表卡片 (Output Files)
        self.files_box = QtWidgets.QGroupBox("产物文件列表 (Output Files)")
        files_box_layout = QtWidgets.QVBoxLayout(self.files_box)
        files_box_layout.setContentsMargins(14, 14, 14, 14)
        files_box_layout.setSpacing(10)

        # 产物操作工具条（多产物时提供一键 ZIP 打包下载）
        self.files_toolbar = QtWidgets.QHBoxLayout()
        self.files_count_lbl = QtWidgets.QLabel("共 0 个产物文件")
        self.files_count_lbl.setObjectName("mutedText")
        self.files_toolbar.addWidget(self.files_count_lbl)
        self.files_toolbar.addStretch()

        self.btn_export_zip = QtWidgets.QPushButton("📦 一键下载全部 (ZIP)")
        self.btn_export_zip.setObjectName("smallButton")
        self.btn_export_zip.setToolTip("将任务所有产物打包为 ZIP 格式导出，保持目录层级结构一致")
        self.btn_export_zip.clicked.connect(self._export_all_as_zip)
        self.btn_export_zip.setVisible(False)
        self.files_toolbar.addWidget(self.btn_export_zip)
        files_box_layout.addLayout(self.files_toolbar)

        self.files_table = QtWidgets.QTableWidget(0, 4)
        self.files_table.verticalHeader().setVisible(False)
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

        # 6. 任务结果数据摘要 (Data Summary)
        self.data_summary_box = QtWidgets.QGroupBox("结果数据摘要 (Data Summary)")
        data_layout = QtWidgets.QVBoxLayout(self.data_summary_box)
        self.data_summary_txt = QtWidgets.QPlainTextEdit()
        self.data_summary_txt.setReadOnly(True)
        self.data_summary_txt.setFixedHeight(120)
        self.data_summary_txt.setObjectName("codeOutput")
        data_layout.addWidget(self.data_summary_txt)
        self.content_layout.addWidget(self.data_summary_box)

        # 7. 脱敏参数摘要
        params_box = QtWidgets.QGroupBox("任务脱敏参数")
        params_layout = QtWidgets.QVBoxLayout(params_box)
        self.params_txt = QtWidgets.QPlainTextEdit()
        self.params_txt.setReadOnly(True)
        self.params_txt.setFixedHeight(100)
        self.params_txt.setObjectName("codeOutput")
        params_layout.addWidget(self.params_txt)
        self.content_layout.addWidget(params_box)

        # 8. 任务报告与执行日志折叠 (Report & Logs)
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
        logs_inner_layout.addWidget(QtWidgets.QLabel("Runtime 报告 (report.md)"))
        logs_inner_layout.addWidget(self.report_txt)

        self.log_txt = QtWidgets.QPlainTextEdit()
        self.log_txt.setReadOnly(True)
        self.log_txt.setFixedHeight(180)
        self.log_txt.setObjectName("logOutput")
        logs_inner_layout.addWidget(QtWidgets.QLabel("任务执行日志 (logs/task.log)"))
        logs_inner_layout.addWidget(self.log_txt)

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
        self.current_result_obj = direct_result
        self.btn_open_sql_select.setVisible(False)
        self.title_task_id_lbl.setText(f"任务详情: {task_id}")

        try:
            task_record = self.runtime.get_task(task_id) or {}
        except Exception as error:
            self.current_task_info = {}
            self.btn_re_execute.setEnabled(False)
            self._render_task_read_error(task_id, error)
            return

        self.current_task_info = task_record
        if not task_record:
            self.btn_re_execute.setEnabled(False)
            self._render_missing_task(task_id)
            return

        status = str(task_record.get("status") or "UNKNOWN").upper()
        self.btn_re_execute.setEnabled(
            bool(task_record.get("command")) and status not in {"PENDING", "RUNNING"}
        )
        self._render_task_metadata(task_record)

        result_missing = False
        result_read_error: str | None = None
        if status in {"PENDING", "RUNNING"}:
            result_dict: dict[str, Any] = {
                "status": status.lower(),
                "message": (
                    "任务已进入 Runtime 队列，正在等待执行。"
                    if status == "PENDING"
                    else "插件 Host 正在执行，等待一次性响应返回。"
                ),
                "data": {},
                "files": [],
                "warnings": [],
            }
        else:
            try:
                persisted_result = self.runtime.get_task_result(task_id)
            except Exception as error:
                persisted_result = None
                result_read_error = str(error)
            if persisted_result is None:
                result_missing = True
                result_dict = {
                    "status": "failed",
                    "message": "任务记录存在，但 result.json 不存在或无法读取。",
                    "data": {"error_code": "RESULT_NOT_FOUND"},
                    "files": [],
                    "warnings": [],
                }
            else:
                result_dict = persisted_result

        # Runtime 任务记录是状态和时间的权威来源。
        duration_text = "耗时: -"
        if elapsed is not None:
            duration_text = f"耗时: {elapsed:.2f}s"
        else:
            started_at = task_record.get("started_at")
            finished_at = task_record.get("finished_at")
            if started_at and finished_at:
                try:
                    started = datetime.fromisoformat(started_at)
                    finished = datetime.fromisoformat(finished_at)
                    duration_text = f"耗时: {(finished - started).total_seconds():.2f}s"
                except (TypeError, ValueError):
                    pass
        self.duration_lbl.setText(duration_text)

        files = result_dict.get("files") or []
        self.btn_open_sql_select.setVisible(
            self._can_open_sql_select(task_record, status, result_dict, files)
        )
        missing_outputs = self._render_files_table(task_id, files)
        if status in {"PENDING", "RUNNING"}:
            self.files_empty_lbl.setText("任务尚未完成，Runtime 返回后才会显示产物。")
        elif result_missing:
            self.files_empty_lbl.setText("result.json 不可用，无法读取产物清单。")

        warnings = result_dict.get("warnings") or []
        if isinstance(warnings, str):
            warnings = [warnings]
        else:
            warnings = [str(item) for item in warnings]
        if missing_outputs:
            warnings.append("以下已声明产物当前缺失，导出已禁用: " + ", ".join(missing_outputs))
        self._render_warnings(warnings)

        display_result = dict(result_dict)
        display_result["warnings"] = warnings
        self._render_status_banner(
            status,
            display_result,
            task_record,
            result_missing=result_missing,
            result_read_error=result_read_error,
        )

        data_value = result_dict.get("data")
        if status in {"PENDING", "RUNNING"}:
            self.data_summary_txt.setPlainText("任务尚未完成，暂无结果数据。")
        elif data_value:
            self.data_summary_txt.setPlainText(json.dumps(data_value, ensure_ascii=False, indent=2))
        else:
            self.data_summary_txt.setPlainText("任务已完成，但未返回结构化数据（Empty result facet）。")
        self.data_summary_box.setVisible(True)

        params = task_record.get("params") or {}
        self.params_txt.setPlainText(json.dumps(params, ensure_ascii=False, indent=2))
        self._load_report_and_log(task_id, status, result_dict, task_record, result_missing)

    @staticmethod
    def _format_task_timestamp(value: Any) -> str:
        if not value:
            return "-"
        try:
            return datetime.fromisoformat(str(value)).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        except (TypeError, ValueError):
            return str(value)

    def _render_task_metadata(self, task_record: dict[str, Any]) -> None:
        self.meta_task_id_lbl.setText(str(task_record.get("id") or self.current_task_id))
        self.meta_command_lbl.setText(str(task_record.get("command") or "-"))
        plugin_name = task_record.get("plugin_name") or "-"
        plugin_version = task_record.get("plugin_version")
        self.meta_plugin_lbl.setText(
            f"{plugin_name} (v{plugin_version})" if plugin_version else str(plugin_name)
        )
        self.meta_started_lbl.setText(self._format_task_timestamp(task_record.get("started_at")))
        self.meta_finished_lbl.setText(self._format_task_timestamp(task_record.get("finished_at")))
        workspace_path = task_record.get("workspace_path")
        self.meta_workspace_lbl.setText(str(workspace_path or "-"))
        self.btn_open_workspace.setEnabled(bool(workspace_path) and Path(workspace_path).is_dir())

    def _render_warnings(self, warnings: list[str]) -> None:
        clean_warnings = [warning.strip() for warning in warnings if warning and warning.strip()]
        self.warnings_box.setVisible(bool(clean_warnings))
        self.warnings_txt.setPlainText("\n".join(f"• {warning}" for warning in clean_warnings))

    def _load_report_and_log(
        self,
        task_id: str,
        status: str,
        result_dict: dict[str, Any],
        task_record: dict[str, Any],
        result_missing: bool,
    ) -> None:
        try:
            report_content = self.runtime.get_task_report(task_id)
        except Exception as error:
            report_content = f"报告读取失败: {error}"
        if not report_content:
            message = result_dict.get("message") or "-"
            report_content = (
                f"--- 任务报告 [{task_id}] ---\n"
                f"状态: {status}\n"
                f"命令: {task_record.get('command') or '-'}\n"
                f"消息: {message}\n"
            )
            if result_missing:
                report_content += "结果: result.json 不存在或无法读取。\n"
        self.report_txt.setPlainText(report_content)

        try:
            task_log = self.runtime.get_task_log(task_id)
        except Exception as error:
            task_log = f"日志读取失败: {error}"
        if not task_log:
            diagnostics = result_dict.get("data") or {}
            task_log = diagnostics.get("task_log_tail") or diagnostics.get("host_stderr") or "当前任务没有可显示的执行日志。"
        self.log_txt.setPlainText(str(task_log))

    def _open_workspace(self) -> None:
        workspace_path = self.current_task_info.get("workspace_path")
        if not workspace_path:
            return
        path = Path(workspace_path)
        if not path.is_dir():
            QtWidgets.QMessageBox.warning(self, "工作区不可用", "任务工作区不存在或已被清理。")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    @staticmethod
    def _apply_style_id(widget: QtWidgets.QWidget, style_id: str) -> None:
        """Refresh QSS after changing a status-specific object selector."""
        widget.setObjectName(style_id)
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _reset_task_sections(self, task_id: str) -> None:
        self.meta_task_id_lbl.setText(task_id)
        self.meta_command_lbl.setText("-")
        self.meta_plugin_lbl.setText("-")
        self.meta_started_lbl.setText("-")
        self.meta_finished_lbl.setText("-")
        self.meta_workspace_lbl.setText("-")
        self.btn_open_workspace.setEnabled(False)
        self._render_warnings([])
        self._render_files_table(task_id, [])
        self.data_summary_txt.setPlainText("无可用结果数据")
        self.data_summary_box.setVisible(True)
        self.params_txt.setPlainText("无可用参数记录")
        self.log_txt.setPlainText("当前任务没有可显示的执行日志。")

    def _render_task_read_error(self, task_id: str, error: Exception) -> None:
        """Render a non-destructive Error facet when Runtime task history cannot be read."""
        self.current_result_obj = None
        self._reset_task_sections(task_id)
        self.status_icon_lbl.setText("❌")
        self.status_main_text.setText("任务记录读取失败")
        self._apply_style_id(self.status_main_text, "statusFailed")
        self.status_sub_text.setText("Runtime 无法读取任务记录；历史数据未被修改。")
        self._apply_style_id(self.status_banner, "statusBannerFailed")
        self.duration_lbl.setText("耗时: -")
        self.stats_row.setVisible(False)
        self.diagnostic_box.setVisible(True)
        self.diag_reason_lbl.setText(f"【TASK_READ_ERROR】{error}")
        self.diag_advice_lbl.setText("建议操作: 返回任务历史后重试；如持续失败，请查看插件与诊断页。")
        self.files_empty_lbl.setText("任务记录读取失败，无法读取产物。")
        self.report_txt.setPlainText(f"--- 任务报告 [{task_id}] ---\nRuntime 任务记录读取失败。\n")

    def _render_missing_task(self, task_id: str):
        """Render a stable, actionable state when Runtime no longer has the task."""
        self.current_result_obj = None
        self._reset_task_sections(task_id)
        self.status_icon_lbl.setText("❓")
        self.status_main_text.setText("找不到任务记录")
        self._apply_style_id(self.status_main_text, "statusFailed")
        self.status_sub_text.setText("该任务可能已被清理，或任务 ID 不再属于当前 Runtime。")
        self._apply_style_id(self.status_banner, "statusBannerFailed")
        self.duration_lbl.setText("耗时: -")
        self.stats_row.setVisible(False)
        self.diagnostic_box.setVisible(True)
        self.diag_reason_lbl.setText(f"【任务 ID: {task_id}】Runtime 未返回任务记录。")
        self.diag_advice_lbl.setText("建议操作: 返回任务历史后刷新列表；如果任务已被清理，请重新执行原命令。")
        self.files_empty_lbl.setText("找不到任务记录，无法读取产物。")
        self.report_txt.setPlainText(f"--- 任务报告 [{task_id}] ---\nRuntime 未返回任务记录。\n")

    def _render_status_banner(
        self,
        status: str,
        result_dict: dict,
        task_record: dict,
        *,
        result_missing: bool = False,
        result_read_error: str | None = None,
    ):
        self.diagnostic_box.setVisible(False)

        data = result_dict.get("data") or {}
        if "success_tables_count" in data or "failed_tables_count" in data or "field_count" in data:
            success_count = data.get("success_tables_count", 0)
            failed_count = data.get("failed_tables_count", 0)
            field_count = data.get("field_count", 0)
            self.stat_success_badge.setText(f"✓ 成功表数: {success_count}")
            self._apply_style_id(self.stat_success_badge, "tagLabel")
            self.stat_failed_badge.setText(f"✗ 失败表数: {failed_count}")
            self._apply_style_id(
                self.stat_failed_badge,
                "tagLabelDanger" if failed_count > 0 else "tagLabelMuted",
            )
            self.stat_fields_badge.setText(f"📊 涉及字段: {field_count}")
            self._apply_style_id(self.stat_fields_badge, "tagLabelInfo")
            self.stats_row.setVisible(True)
        else:
            self.stats_row.setVisible(False)

        warnings = result_dict.get("warnings") or []
        has_warnings = bool(warnings)
        is_empty_result = not data and not (result_dict.get("files") or [])

        if status == "PENDING":
            self.status_icon_lbl.setText("⏳")
            self.status_main_text.setText("任务等待执行 (PENDING)")
            self._apply_style_id(self.status_main_text, "statusWarning")
            self.status_sub_text.setText("任务已由 Runtime 记录，正在等待进入插件 Host。")
            self._apply_style_id(self.status_banner, "statusBannerWarning")

        elif status == "RUNNING":
            self.status_icon_lbl.setText("⏳")
            self.status_main_text.setText("任务正在运行 (RUNNING)")
            self._apply_style_id(self.status_main_text, "statusWarning")
            self.status_sub_text.setText("插件 Host 正在执行；当前协议仅在完成时返回一次响应。")
            self._apply_style_id(self.status_banner, "statusBannerWarning")

        elif status == "SUCCEEDED":
            if result_missing:
                self.status_icon_lbl.setText("⚠️")
                self.status_main_text.setText("任务状态成功，但结果不可用 (SUCCEEDED)")
                self._apply_style_id(self.status_main_text, "statusWarning")
                self.status_sub_text.setText("Runtime 记录为成功，但 result.json 不存在或无法读取。")
                self._apply_style_id(self.status_banner, "statusBannerWarning")
            elif has_warnings:
                self.status_icon_lbl.setText("⚠️")
                self.status_main_text.setText("任务执行成功但包含警告 (SUCCEEDED)")
                self._apply_style_id(self.status_main_text, "statusWarning")
                self.status_sub_text.setText(result_dict.get("message") or "任务已完成，请检查警告区。")
                self._apply_style_id(self.status_banner, "statusBannerWarning")
            elif is_empty_result:
                self.status_icon_lbl.setText("✅")
                self.status_main_text.setText("任务执行成功，无返回数据或产物 (SUCCEEDED)")
                self._apply_style_id(self.status_main_text, "statusSuccess")
                self.status_sub_text.setText(result_dict.get("message") or "任务已完成，但结果为空。")
                self._apply_style_id(self.status_banner, "statusBannerSuccess")
            else:
                self.status_icon_lbl.setText("✅")
                self.status_main_text.setText("任务执行成功 (SUCCEEDED)")
                self._apply_style_id(self.status_main_text, "statusSuccess")
                self.status_sub_text.setText(result_dict.get("message") or "所有步骤已顺利完成，产物已落盘。")
                self._apply_style_id(self.status_banner, "statusBannerSuccess")

        elif status == "FAILED":
            self.status_icon_lbl.setText("❌")
            self.status_main_text.setText("任务执行失败 (FAILED)")
            self._apply_style_id(self.status_main_text, "statusFailed")
            error_message = result_dict.get("message") or "插件执行过程中遇到错误。"
            self.status_sub_text.setText(error_message)
            self._apply_style_id(self.status_banner, "statusBannerFailed")
            self.diagnostic_box.setVisible(True)
            error_code = task_record.get("error_code") or data.get("error_code") or "PLUGIN_ERROR"
            self.diag_reason_lbl.setText(f"【错误码: {error_code}】 {error_message}")
            self.diag_advice_lbl.setText("建议操作: 检查输入参数、源文件和依赖后，修改参数并重新执行。")

        elif status == "CANCELLED":
            self.status_icon_lbl.setText("⏹️")
            self.status_main_text.setText("任务已取消 (CANCELLED)")
            self._apply_style_id(self.status_main_text, "statusCancelled")
            self.status_sub_text.setText(result_dict.get("message") or "任务未继续执行；已落盘内容仍可查看。")
            self._apply_style_id(self.status_banner, "statusBannerCancelled")

        elif status == "ABANDONED":
            self.status_icon_lbl.setText("⚠️")
            self.status_main_text.setText("任务异常中断 (ABANDONED)")
            self._apply_style_id(self.status_main_text, "statusAbandoned")
            self.status_sub_text.setText("任务宿主进程非正常退出或超时中断。")
            self._apply_style_id(self.status_banner, "statusBannerAbandoned")
            self.diagnostic_box.setVisible(True)
            self.diag_reason_lbl.setText(
                f"【错误码: {task_record.get('error_code') or 'HOST_INTERRUPTED'}】进程执行中断"
            )
            self.diag_advice_lbl.setText("建议操作: 查看日志确认超时、崩溃或资源问题后再重试。")

        else:
            self.status_icon_lbl.setText("❓")
            self.status_main_text.setText(f"未知 Runtime 状态: {status}")
            self._apply_style_id(self.status_main_text, "statusFailed")
            self.status_sub_text.setText(result_dict.get("message") or "Runtime 返回了当前 GUI 不识别的状态。")
            self._apply_style_id(self.status_banner, "statusBannerFailed")
            self.diagnostic_box.setVisible(True)
            self.diag_reason_lbl.setText(f"【UNKNOWN_TASK_STATUS】{status}")
            self.diag_advice_lbl.setText("建议操作: 查看 Runtime 与 GUI 版本是否一致。")

        if result_missing and status != "FAILED":
            self.diagnostic_box.setVisible(True)
            detail = f": {result_read_error}" if result_read_error else ""
            self.diag_reason_lbl.setText(f"【RESULT_NOT_FOUND】result.json 不存在或无法读取{detail}")
            self.diag_advice_lbl.setText("建议操作: 打开工作区检查任务证据；不要将此展示问题视为新的 Runtime 状态。")

    def _render_files_table(self, task_id: str, files: list[str]) -> list[str]:
        self.files_table.setRowCount(0)
        self.files_empty_lbl.setText("本任务未产生输出文件。")
        self.current_files = list(files)
        if not files:
            self.files_table.setVisible(False)
            self.files_empty_lbl.setVisible(True)
            self.files_count_lbl.setText("共 0 个产物文件")
            self.btn_export_zip.setVisible(False)
            return []

        self.files_table.setVisible(True)
        self.files_empty_lbl.setVisible(False)
        self.files_count_lbl.setText(f"共 {len(files)} 个产物文件")
        self.btn_export_zip.setVisible(True)
        self.files_table.setRowCount(len(files))

        workspace_path = self.current_task_info.get("workspace_path")
        output_root = (Path(workspace_path) / "output").resolve() if workspace_path else None
        missing_outputs: list[str] = []

        for row, rel_path in enumerate(files):
            rel_path = str(rel_path)
            file_name = Path(rel_path).name
            full_file_path: Path | None = None
            if output_root is not None:
                candidate = (output_root / rel_path).resolve()
                try:
                    candidate.relative_to(output_root)
                except ValueError:
                    candidate = None
                full_file_path = candidate
            file_exists = bool(full_file_path and full_file_path.is_file())
            if not file_exists:
                missing_outputs.append(rel_path)

            name_item = QtWidgets.QTableWidgetItem(f"📄 {file_name}")
            name_item.setToolTip(rel_path)
            self.files_table.setItem(row, 0, name_item)
            self.files_table.setItem(row, 1, QtWidgets.QTableWidgetItem(rel_path))

            if file_exists and full_file_path is not None:
                size_kb = full_file_path.stat().st_size / 1024.0
                size_text = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{(size_kb / 1024):.2f} MB"
            else:
                size_text = "文件缺失"
            self.files_table.setItem(row, 2, QtWidgets.QTableWidgetItem(size_text))

            actions_widget = QtWidgets.QWidget()
            actions_layout = QtWidgets.QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(6)

            export_btn = QtWidgets.QPushButton("💾 导出...")
            export_btn.setObjectName("smallButton")
            export_btn.setEnabled(file_exists)
            if not file_exists:
                export_btn.setToolTip("已声明的产物文件不存在，无法导出。")
            export_btn.clicked.connect(
                lambda _, rp=rel_path, fn=file_name: self._export_single_file(task_id, rp, fn)
            )
            actions_layout.addWidget(export_btn)

            if file_name.lower().endswith((".png", ".jpg", ".jpeg")):
                annotation_btn = QtWidgets.QPushButton("✏️ 标注")
                annotation_btn.setObjectName("smallButton")
                annotation_btn.setEnabled(file_exists)
                annotation_path = str(full_file_path) if full_file_path else ""
                annotation_btn.clicked.connect(
                    lambda _, fp=annotation_path: self.openAnnotation.emit(fp)
                )
                actions_layout.addWidget(annotation_btn)

            self.files_table.setCellWidget(row, 3, actions_widget)

        status = str(self.current_task_info.get("status") or "").upper()
        self.btn_export_zip.setEnabled(status == "SUCCEEDED" and not missing_outputs)
        if missing_outputs:
            self.btn_export_zip.setToolTip("部分声明产物已缺失，不能打包导出。")
        else:
            self.btn_export_zip.setToolTip("将任务所有产物打包为 ZIP 格式导出，保持目录层级结构一致")
        return missing_outputs

    def _export_single_file(self, task_id: str, rel_path: str, filename: str):
        """调用 Runtime.commit_output 导出文件到用户选定路径，绝不在 UI 中私自复制"""
        dest_path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出任务产物", filename, options=_file_dialog_options())
        if not dest_path:
            return
        try:
            self.runtime.commit_output(task_id, rel_path, Path(dest_path))
            QtWidgets.QMessageBox.information(self, "导出成功", f"文件已成功导出至:\n{dest_path}")
        except PermissionError as error:
            QtWidgets.QMessageBox.warning(
                self,
                "没有导出权限",
                f"目标位置不可写，请选择其他目录或调整系统权限后重试。\n{error}",
            )
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "导出失败", f"导出文件时发生异常:\n{error}")

    def _export_all_as_zip(self):
        """一键将当前任务所有产物打包导出为 ZIP 格式，内部目录结构与工作区保持完全一致"""
        task_id = self.current_task_id
        if not task_id:
            return
        default_name = f"{task_id}-outputs.zip"
        dest_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "一键打包导出全部产物",
            default_name,
            "ZIP 压缩包 (*.zip);;所有文件 (*.*)",
            options=_file_dialog_options(),
        )
        if not dest_path:
            return
        try:
            self.runtime.commit_outputs_archive(task_id, Path(dest_path))
            QtWidgets.QMessageBox.information(self, "导出成功", f"所有产物已成功打包导出至:\n{dest_path}")
        except PermissionError as error:
            QtWidgets.QMessageBox.warning(
                self,
                "没有导出权限",
                f"目标位置不可写，请选择其他目录或调整系统权限后重试。\n{error}",
            )
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "导出失败", f"打包导出产物时发生异常:\n{error}")

    @staticmethod
    def _can_open_sql_select(
        task_record: dict[str, Any],
        status: str,
        result_dict: dict[str, Any],
        files: list[str],
    ) -> bool:
        if task_record.get("command") != "sql.parse" or status != "SUCCEEDED":
            return False
        if result_dict.get("status") != "success":
            return False
        output_file = result_dict.get("data", {}).get("output_file")
        if not isinstance(output_file, str) or output_file not in files:
            return False
        return Path(output_file).suffix.lower() in {".json", ".csv", ".xlsx"}

    def _on_open_sql_select(self):
        """Open sql.select with a Runtime-validated parse artifact, never execute it."""
        result = self.runtime.get_task_result(self.current_task_id) or {}
        output_file = result.get("data", {}).get("output_file")
        if not isinstance(output_file, str):
            QtWidgets.QMessageBox.warning(self, "无法继续", "未找到 sql.parse 声明的字段清单产物。")
            return
        try:
            artifact_path = self.runtime.get_task_output_path(self.current_task_id, output_file)
        except (LookupError, ValueError, OSError) as error:
            QtWidgets.QMessageBox.warning(
                self,
                "无法继续",
                f"字段清单产物不可用，请检查任务工作区后重试。\n{error}",
            )
            return
        self.sqlSelectRequested.emit(
            "sql.select",
            {"input": str(artifact_path), "input_format": artifact_path.suffix.lower().lstrip(".")},
        )

    def _on_re_execute(self):
        cmd = self.current_task_info.get("command")
        params = self.current_task_info.get("params") or {}
        if cmd:
            self.reExecuteRequested.emit(cmd, params)


# ==============================================================================
# 视图 5：任务历史 - 仅通过 Runtime 访问
# ==============================================================================

class TaskHistoryView(QtWidgets.QWidget):
    """Runtime-driven task history with explicit loading, empty, and error states."""

    taskSelected = QtCore.Signal(str)
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

        header = QtWidgets.QHBoxLayout()
        title_box = QtWidgets.QVBoxLayout()
        title_lbl = QtWidgets.QLabel("任务历史")
        title_lbl.setObjectName("pageTitle")
        sub_lbl = QtWidgets.QLabel("查看由 Runtime 记录的任务执行工作区、状态及结果")
        sub_lbl.setObjectName("mutedText")
        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)
        header.addLayout(title_box)
        header.addStretch()

        clean_history_btn = QtWidgets.QPushButton("🧹 清理工作区")
        clean_history_btn.setObjectName("secondaryButton")
        clean_history_btn.setToolTip("按日期清理任务工作区；删除审计记录需要单独勾选和确认")
        clean_history_btn.clicked.connect(self._on_clean_workspace)
        header.addWidget(clean_history_btn)

        refresh_btn = QtWidgets.QPushButton("🔄 刷新")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.clicked.connect(self.refresh_data)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        filter_bar = QtWidgets.QHBoxLayout()
        filter_bar.setSpacing(10)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索任务 ID...")
        self.search_input.textChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.search_input, 2)

        self.status_combo = QtWidgets.QComboBox()
        self.status_combo.addItem("全部状态", None)
        self.status_combo.addItem("⏳ 排队中（PENDING）", "PENDING")
        self.status_combo.addItem("▶️ 运行中（RUNNING）", "RUNNING")
        self.status_combo.addItem("✅ 成功（SUCCEEDED）", "SUCCEEDED")
        self.status_combo.addItem("❌ 失败（FAILED）", "FAILED")
        self.status_combo.addItem("⏹️ 已取消（CANCELLED）", "CANCELLED")
        self.status_combo.addItem("⚠️ 异常中断（ABANDONED）", "ABANDONED")
        self.status_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.status_combo, 1)

        self.command_combo = QtWidgets.QComboBox()
        self.command_combo.addItem("全部命令", None)
        self.command_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.command_combo, 1)

        self.time_combo = QtWidgets.QComboBox()
        self.time_combo.addItem("全部时间", None)
        self.time_combo.addItem("最近 24 小时", 1)
        self.time_combo.addItem("最近 7 天", 7)
        self.time_combo.addItem("最近 30 天", 30)
        self.time_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.time_combo, 1)
        layout.addLayout(filter_bar)

        self.table = QtWidgets.QTableWidget(0, 9)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalHeaderLabels(
            ["任务 ID", "命令", "插件 / 版本", "状态", "开始时间", "耗时", "产物数", "完成时间", "操作"]
        )
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        for column in range(self.table.columnCount()):
            mode = QtWidgets.QHeaderView.ResizeMode.Stretch if column == 1 else QtWidgets.QHeaderView.ResizeMode.ResizeToContents
            self.table.horizontalHeader().setSectionResizeMode(column, mode)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        self.table_stack = QtWidgets.QStackedWidget()
        self.loading_state_lbl = QtWidgets.QLabel("正在通过 Runtime 加载任务历史…")
        self.loading_state_lbl.setObjectName("mutedText")
        self.loading_state_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.table_stack.addWidget(self.loading_state_lbl)
        self.table_stack.addWidget(self.table)

        self.empty_state_lbl = QtWidgets.QLabel()
        self.empty_state_lbl.setObjectName("mutedText")
        self.empty_state_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.empty_state_lbl.setWordWrap(True)
        self.table_stack.addWidget(self.empty_state_lbl)

        error_page = QtWidgets.QWidget()
        error_layout = QtWidgets.QVBoxLayout(error_page)
        error_layout.addStretch()
        self.error_state_lbl = QtWidgets.QLabel()
        self.error_state_lbl.setObjectName("mutedText")
        self.error_state_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.error_state_lbl.setWordWrap(True)
        error_layout.addWidget(self.error_state_lbl)
        retry_btn = QtWidgets.QPushButton("重试")
        retry_btn.setObjectName("secondaryButton")
        retry_btn.clicked.connect(self.refresh_data)
        error_layout.addWidget(retry_btn, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        error_layout.addStretch()
        self.table_stack.addWidget(error_page)
        layout.addWidget(self.table_stack, 1)

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

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        if not value:
            return "-"
        text = str(value).replace("T", " ")
        if "." in text:
            text = text.split(".", 1)[0]
        return text

    @classmethod
    def _format_duration(cls, task: dict[str, Any]) -> str:
        started = cls._parse_timestamp(task.get("started_at"))
        finished = cls._parse_timestamp(task.get("finished_at"))
        if started is None or finished is None:
            return "-"
        try:
            seconds = max(0.0, (finished - started).total_seconds())
        except TypeError:
            return "-"
        if seconds < 60:
            return f"{seconds:.2f}s"
        minutes, seconds = divmod(int(seconds), 60)
        if minutes < 60:
            return f"{minutes}m {seconds}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"

    @staticmethod
    def _status_label(status: str) -> str:
        labels = {
            "PENDING": "排队中（PENDING）",
            "RUNNING": "运行中（RUNNING）",
            "SUCCEEDED": "成功（SUCCEEDED）",
            "FAILED": "失败（FAILED）",
            "CANCELLED": "已取消（CANCELLED）",
            "ABANDONED": "异常中断（ABANDONED）",
        }
        return labels.get(status, status or "未知")

    def populate_command_filter(self):
        current_cmd = self.command_combo.currentData()
        self.command_combo.blockSignals(True)
        self.command_combo.clear()
        self.command_combo.addItem("全部命令", None)
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
        self.table_stack.setCurrentIndex(0)
        self.count_lbl.setText("正在加载…")
        self.btn_prev.setEnabled(False)
        self.btn_next.setEnabled(False)

        try:
            self.populate_command_filter()
            status = self.status_combo.currentData()
            command = self.command_combo.currentData()
            query = self.search_input.text().strip()
            days = self.time_combo.currentData()
            has_filter = bool(query or status or command or days)
            started_from = (datetime.now(UTC) - timedelta(days=days)).isoformat() if days else None
            filters = {
                "status": status,
                "command": command,
                "task_id_query": query or None,
                "started_from": started_from,
            }
            self.total_count = self.runtime.count_tasks(**filters)
            max_page = max(1, (self.total_count + self.page_size - 1) // self.page_size)
            if self.current_page >= max_page:
                self.current_page = max_page - 1
            offset = self.current_page * self.page_size
            tasks = self.runtime.list_tasks(
                **filters,
                limit=self.page_size,
                offset=offset,
            )
        except Exception as error:
            self.table.setRowCount(0)
            self.error_state_lbl.setText(
                "任务历史读取失败。Runtime 数据未被修改，也不会自动删除数据库。\n"
                f"请重试或打开“插件与诊断”查看运行路径。\n\n{error}"
            )
            self.table_stack.setCurrentIndex(3)
            self.count_lbl.setText("任务历史读取失败")
            self.page_lbl.setText("第 - 页")
            return

        self.table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            self._render_task_row(row, task)

        if tasks:
            self.table_stack.setCurrentIndex(1)
        else:
            if has_filter:
                self.empty_state_lbl.setText("当前筛选条件没有结果。\n可清除任务 ID、状态、命令或时间筛选后重试。")
            else:
                self.empty_state_lbl.setText("暂无任务历史。\n任务执行后，Runtime 会在这里保留审计记录和结果入口。")
            self.table_stack.setCurrentIndex(2)

        max_page = max(1, (self.total_count + self.page_size - 1) // self.page_size)
        self.page_lbl.setText(f"第 {self.current_page + 1} / {max_page} 页")
        self.count_lbl.setText(f"共 {self.total_count} 条记录")
        self.btn_prev.setEnabled(self.current_page > 0)
        self.btn_next.setEnabled((self.current_page + 1) * self.page_size < self.total_count)

    def _render_task_row(self, row: int, task: dict[str, Any]):
        task_id = str(task.get("id", ""))
        command = str(task.get("command", ""))
        plugin_name = str(task.get("plugin_name", ""))
        plugin_version = str(task.get("plugin_version", ""))
        status = str(task.get("status", "")).upper()

        id_item = QtWidgets.QTableWidgetItem(task_id)
        id_item.setFont(QtGui.QFont("monospace", 11))
        self.table.setItem(row, 0, id_item)
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(command))
        plugin_text = plugin_name if not plugin_version else f"{plugin_name} v{plugin_version}"
        self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(plugin_text))

        status_item = QtWidgets.QTableWidgetItem(self._status_label(status))
        color = {
            "SUCCEEDED": "#10b981",
            "FAILED": "#ef4444",
            "ABANDONED": "#ea580c",
            "RUNNING": "#2563eb",
            "PENDING": "#7c3aed",
        }.get(status, "#64748b")
        status_item.setForeground(QtGui.QColor(color))
        self.table.setItem(row, 3, status_item)
        self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(self._format_timestamp(task.get("started_at"))))
        self.table.setItem(row, 5, QtWidgets.QTableWidgetItem(self._format_duration(task)))

        output_count = "-"
        try:
            result = self.runtime.get_task_result(task_id)
            if result is not None and isinstance(result.get("files"), list):
                output_count = str(len(result["files"]))
        except (OSError, ValueError, TypeError):
            output_count = "-"
        self.table.setItem(row, 6, QtWidgets.QTableWidgetItem(output_count))
        self.table.setItem(row, 7, QtWidgets.QTableWidgetItem(self._format_timestamp(task.get("finished_at"))))

        action_widget = QtWidgets.QWidget()
        actions = QtWidgets.QHBoxLayout(action_widget)
        actions.setContentsMargins(4, 2, 4, 2)
        actions.setSpacing(6)
        view_btn = QtWidgets.QPushButton("查看详情")
        view_btn.setObjectName("smallButton")
        view_btn.clicked.connect(lambda _, tid=task_id: self.taskSelected.emit(tid))
        actions.addWidget(view_btn)
        reopen_btn = QtWidgets.QPushButton("打开配置")
        reopen_btn.setObjectName("smallButton")
        reopen_btn.setToolTip("仅将历史参数回填到命令工作台，不会自动提交执行")
        params = dict(task.get("params") or {})
        reopen_btn.clicked.connect(
            lambda _, cmd=command, values=params: self.reExecuteRequested.emit(cmd, dict(values))
        )
        actions.addWidget(reopen_btn)
        self.table.setCellWidget(row, 8, action_widget)

    def _on_cell_double_clicked(self, row: int, col: int):
        id_item = self.table.item(row, 0)
        if id_item:
            self.taskSelected.emit(id_item.text())

    def _on_clean_workspace(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("清理任务工作区")
        dialog.setMinimumWidth(440)
        d_layout = QtWidgets.QVBoxLayout(dialog)
        d_layout.setContentsMargins(20, 20, 20, 20)
        d_layout.setSpacing(14)

        desc_lbl = QtWidgets.QLabel(
            "选择截止日期。默认仅删除该日期之前的任务工作区文件；"
            "历史审计记录会保留，除非单独勾选删除。"
        )
        desc_lbl.setWordWrap(True)
        d_layout.addWidget(desc_lbl)

        date_row = QtWidgets.QHBoxLayout()
        date_lbl = QtWidgets.QLabel("清理此本地日期零点之前:")
        date_lbl.setStyleSheet("font-weight: 600;")
        date_row.addWidget(date_lbl)
        date_edit = QtWidgets.QDateEdit()
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QtCore.QDate.currentDate())
        date_row.addWidget(date_edit)
        date_row.addStretch()
        d_layout.addLayout(date_row)

        clean_history_check = QtWidgets.QCheckBox("同时永久删除对应历史审计记录")
        clean_history_check.setChecked(False)
        clean_history_check.setToolTip("高影响操作：删除后任务将不再出现在历史列表中")
        d_layout.addWidget(clean_history_check)

        btn_box = QtWidgets.QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.setObjectName("secondaryButton")
        btn_cancel.clicked.connect(dialog.reject)
        btn_box.addWidget(btn_cancel)
        btn_confirm = QtWidgets.QPushButton("继续确认")
        btn_confirm.setObjectName("primaryButton")

        def _do_clean():
            qdate = date_edit.date()
            target_date = date(qdate.year(), qdate.month(), qdate.day())
            delete_history = clean_history_check.isChecked()
            impact = f"永久删除本地时间 {target_date.isoformat()} 00:00 之前的任务工作区文件。"
            if delete_history:
                impact += "\n同时永久删除对应历史审计记录。"
            else:
                impact += "\n历史审计记录将保留。"
            reply = QtWidgets.QMessageBox.question(
                dialog,
                "最终确认清理范围",
                impact + "\n\n是否继续？",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            dialog.accept()
            try:
                cleaned_count = self.runtime.clean_workspace(target_date)
                message = f"已清理 {cleaned_count} 个工作区。"
                if delete_history:
                    history_cleaned = self.runtime.clean_history(target_date)
                    message += f"\n已删除 {history_cleaned} 条历史审计记录。"
                QtWidgets.QMessageBox.information(self, "清理完成", message)
                self.refresh_data()
            except Exception as error:
                QtWidgets.QMessageBox.critical(self, "清理失败", f"Runtime 清理过程中发生异常:\n{error}")

        btn_confirm.clicked.connect(_do_clean)
        btn_box.addWidget(btn_confirm)
        d_layout.addLayout(btn_box)
        dialog.exec()


# ==============================================================================
# 视图 6：插件诊断与系统设置
# ==============================================================================

class PluginDiagnosticsView(QtWidgets.QWidget):
    """Read-only plugin and Runtime diagnostics plus separately confirmed mutations."""

    def __init__(self, runtime: Runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self._init_ui()

    def _init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        header = QtWidgets.QHBoxLayout()
        title_box = QtWidgets.QVBoxLayout()
        title_lbl = QtWidgets.QLabel("插件诊断与状态")
        title_lbl.setObjectName("pageTitle")
        sub_lbl = QtWidgets.QLabel("分区查看可用插件、不可用插件与 Runtime 只读诊断")
        sub_lbl.setObjectName("mutedText")
        title_box.addWidget(title_lbl)
        title_box.addWidget(sub_lbl)
        header.addLayout(title_box)
        header.addStretch()

        self.import_btn = QtWidgets.QPushButton("📥 导入插件")
        self.import_btn.setObjectName("primaryButton")
        self.import_btn.setToolTip("先由 Runtime 校验并预览 ZIP，再确认安装或覆盖")
        self.import_btn.clicked.connect(self._on_import_plugin)
        header.addWidget(self.import_btn)

        self.uninstall_btn = QtWidgets.QPushButton("🗑️ 卸载选中插件")
        self.uninstall_btn.setObjectName("secondaryButton")
        self.uninstall_btn.setEnabled(False)
        self.uninstall_btn.clicked.connect(self._on_uninstall_plugin)
        header.addWidget(self.uninstall_btn)

        refresh_btn = QtWidgets.QPushButton("🔄 重新扫描插件")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.clicked.connect(self.refresh_plugins)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        available_group = QtWidgets.QGroupBox("可用插件")
        available_layout = QtWidgets.QVBoxLayout(available_group)
        self.plugins_table = QtWidgets.QTableWidget(0, 7)
        self.plugins_table.verticalHeader().setVisible(False)
        self.plugins_table.setHorizontalHeaderLabels(
            ["插件名称", "版本", "分类", "命令列表", "能力", "Core 兼容", "路径"]
        )
        for column in range(self.plugins_table.columnCount()):
            mode = QtWidgets.QHeaderView.ResizeMode.Stretch if column in {3, 6} else QtWidgets.QHeaderView.ResizeMode.ResizeToContents
            self.plugins_table.horizontalHeader().setSectionResizeMode(column, mode)
        self.plugins_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.plugins_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.plugins_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.plugins_table.itemSelectionChanged.connect(self._on_plugin_selection_changed)

        self.available_stack = QtWidgets.QStackedWidget()
        available_loading = QtWidgets.QLabel("正在通过 Runtime 扫描插件…")
        available_loading.setObjectName("mutedText")
        available_loading.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.available_stack.addWidget(available_loading)
        self.available_stack.addWidget(self.plugins_table)
        self.available_empty = QtWidgets.QLabel("没有发现可用插件。请检查插件目录或下方问题列表。")
        self.available_empty.setObjectName("mutedText")
        self.available_empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.available_empty.setWordWrap(True)
        self.available_stack.addWidget(self.available_empty)
        self.available_error = QtWidgets.QLabel()
        self.available_error.setObjectName("mutedText")
        self.available_error.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.available_error.setWordWrap(True)
        self.available_stack.addWidget(self.available_error)
        available_layout.addWidget(self.available_stack)
        layout.addWidget(available_group, 2)

        unavailable_group = QtWidgets.QGroupBox("不可用插件与 Schema / 兼容性问题")
        unavailable_layout = QtWidgets.QVBoxLayout(unavailable_group)
        self.unavailable_table = QtWidgets.QTableWidget(0, 2)
        self.unavailable_table.verticalHeader().setVisible(False)
        self.unavailable_table.setHorizontalHeaderLabels(["插件路径", "具体原因"])
        self.unavailable_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.unavailable_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.unavailable_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.unavailable_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.unavailable_stack = QtWidgets.QStackedWidget()
        self.unavailable_stack.addWidget(self.unavailable_table)
        unavailable_empty = QtWidgets.QLabel("未发现 manifest、Schema 或兼容性问题。")
        unavailable_empty.setObjectName("mutedText")
        unavailable_empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.unavailable_stack.addWidget(unavailable_empty)
        unavailable_layout.addWidget(self.unavailable_stack)
        layout.addWidget(unavailable_group, 1)

        diagnostics_group = QtWidgets.QGroupBox("Runtime 只读诊断")
        diagnostics_layout = QtWidgets.QFormLayout(diagnostics_group)
        diagnostics_layout.setContentsMargins(14, 14, 14, 14)
        self.runtime_diagnostic_labels: dict[str, QtWidgets.QLabel] = {}
        diagnostic_rows = [
            ("version", "TestBox 版本"),
            ("runtime_root", "Runtime 根路径"),
            ("workspace_dir", "任务工作区"),
            ("plugins_dir", "用户插件目录"),
            ("bundled_plugins_dir", "内置插件目录"),
            ("host_protocol", "Host 协议"),
        ]
        for key, caption in diagnostic_rows:
            value_lbl = QtWidgets.QLabel("-")
            value_lbl.setObjectName("mutedText")
            value_lbl.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            value_lbl.setWordWrap(True)
            diagnostics_layout.addRow(f"{caption}:", value_lbl)
            self.runtime_diagnostic_labels[key] = value_lbl
        boundary_note = QtWidgets.QLabel(
            "Plugin Host 提供进程级异常隔离，不是恶意代码安全沙箱；本地插件仍按可信代码处理。"
        )
        boundary_note.setWordWrap(True)
        boundary_note.setObjectName("mutedText")
        diagnostics_layout.addRow("隔离边界:", boundary_note)
        layout.addWidget(diagnostics_group)

        clean_box = QtWidgets.QGroupBox("高影响操作：工作区清理")
        clean_layout = QtWidgets.QHBoxLayout(clean_box)
        clean_layout.setContentsMargins(16, 16, 16, 16)
        clean_layout.setSpacing(12)
        clean_layout.addWidget(QtWidgets.QLabel("清理此本地日期零点之前的任务工作区:"))
        self.date_picker = QtWidgets.QDateEdit()
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDate(QtCore.QDate.currentDate())
        clean_layout.addWidget(self.date_picker)
        clean_layout.addStretch()
        self.btn_clean = QtWidgets.QPushButton("🧹 执行清理")
        self.btn_clean.setObjectName("secondaryButton")
        self.btn_clean.clicked.connect(self._on_clean_workspace)
        clean_layout.addWidget(self.btn_clean)
        layout.addWidget(clean_box)

        self.refresh_plugins()

    def refresh_plugins(self):
        self.available_stack.setCurrentIndex(0)
        self.uninstall_btn.setEnabled(False)
        try:
            plugins = self.runtime.list_plugins()
            unavailable = self.runtime.list_unavailable_plugins()
            runtime_info = self.runtime.get_runtime_diagnostics()
            schema_issues: list[dict[str, str]] = []
            for plugin in plugins:
                for command in plugin.commands:
                    try:
                        self.runtime.get_command_schema(command.name)
                    except Exception as error:
                        schema_issues.append(
                            {
                                "path": str(plugin.path),
                                "reason": f"Schema {command.input_schema or command.name}: {error}",
                            }
                        )
            unavailable = [*unavailable, *schema_issues]
        except Exception as error:
            self.plugins_table.setRowCount(0)
            self.unavailable_table.setRowCount(0)
            self.available_error.setText(
                "插件诊断读取失败。Runtime 和插件目录未被修改。\n"
                f"请修正问题后重新扫描。\n\n{error}"
            )
            self.available_stack.setCurrentIndex(3)
            self.unavailable_stack.setCurrentIndex(1)
            return

        self.plugins_table.setRowCount(len(plugins))
        for row, plugin in enumerate(plugins):
            details = self.runtime.inspect_plugin(plugin.name) or {}
            name_item = QtWidgets.QTableWidgetItem(f"📦 {plugin.name}")
            name_item.setData(QtCore.Qt.ItemDataRole.UserRole, details)
            self.plugins_table.setItem(row, 0, name_item)
            self.plugins_table.setItem(row, 1, QtWidgets.QTableWidgetItem(plugin.version))
            category = f"{CATEGORY_LABELS.get(plugin.category, plugin.category)}（{plugin.category.upper()}）"
            self.plugins_table.setItem(row, 2, QtWidgets.QTableWidgetItem(category))
            command_names = [command.name for command in plugin.commands]
            self.plugins_table.setItem(row, 3, QtWidgets.QTableWidgetItem(", ".join(command_names)))
            capabilities = plugin.capabilities or {}
            capability_text = ", ".join(f"{key}={value}" for key, value in sorted(capabilities.items())) or "-"
            self.plugins_table.setItem(row, 4, QtWidgets.QTableWidgetItem(capability_text))
            self.plugins_table.setItem(row, 5, QtWidgets.QTableWidgetItem(plugin.core_compatibility))
            self.plugins_table.setItem(row, 6, QtWidgets.QTableWidgetItem(str(details.get("path", plugin.path))))
        self.available_stack.setCurrentIndex(1 if plugins else 2)

        self.unavailable_table.setRowCount(len(unavailable))
        for row, item in enumerate(unavailable):
            self.unavailable_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(item.get("path", "-"))))
            self.unavailable_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(item.get("reason", "未知原因"))))
        self.unavailable_stack.setCurrentIndex(0 if unavailable else 1)

        for key, label in self.runtime_diagnostic_labels.items():
            label.setText(str(runtime_info.get(key, "-")))

    def _selected_plugin_details(self) -> dict[str, Any] | None:
        selected = self.plugins_table.selectedItems()
        if not selected:
            return None
        name_item = self.plugins_table.item(selected[0].row(), 0)
        details = name_item.data(QtCore.Qt.ItemDataRole.UserRole) if name_item else None
        return details if isinstance(details, dict) else None

    def _on_plugin_selection_changed(self):
        details = self._selected_plugin_details()
        self.uninstall_btn.setEnabled(bool(details and details.get("uninstallable")))
        if details and not details.get("uninstallable"):
            self.uninstall_btn.setToolTip("内置或只读插件不能卸载")
        else:
            self.uninstall_btn.setToolTip("卸载用户插件；历史任务不会被删除")

    def _on_import_plugin(self):
        source, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择插件包",
            "",
            "插件包 (*.zip);;所有文件 (*.*)",
            options=_file_dialog_options(),
        )
        if not source:
            return

        source_path = Path(source)
        try:
            preview = self.runtime.preview_plugin_install(source_path)
            installed = self.runtime.inspect_plugin(str(preview["name"]))
        except (PluginPackageError, OSError, ValueError) as error:
            QtWidgets.QMessageBox.critical(self, "插件包校验失败", str(error))
            return

        command_text = ", ".join(preview.get("commands") or []) or "无"
        action = "覆盖安装" if installed else "安装"
        impact = (
            f"插件: {preview['name']} v{preview['version']}\n"
            f"命令: {command_text}\n"
            f"来源: {source_path}\n\n"
            f"确认{action}到用户插件目录吗？历史任务不会被删除。"
        )
        reply = QtWidgets.QMessageBox.question(
            self,
            f"确认{action}插件",
            impact,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        try:
            manifest = self.runtime.install_plugin(source_path, force=bool(installed))
        except (PluginPackageError, OSError, ValueError) as error:
            QtWidgets.QMessageBox.critical(self, "导入插件失败", str(error))
            return

        self.refresh_plugins()
        QtWidgets.QMessageBox.information(
            self,
            "导入成功",
            f"插件 {manifest.name} v{manifest.version} 已{action}。\n工具目录已同步更新。",
        )

    def _on_uninstall_plugin(self):
        details = self._selected_plugin_details()
        if not details or not details.get("uninstallable"):
            return
        name = str(details.get("name", ""))
        version = str(details.get("version", ""))
        commands = ", ".join(item.get("name", "") for item in details.get("commands", [])) or "无"
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认卸载插件",
            f"插件: {name} v{version}\n路径: {details.get('path', '-')}\n将移除命令: {commands}\n\n"
            "卸载后工具目录会更新，但历史任务和审计记录不会被删除。是否继续？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self.runtime.uninstall_plugin(name)
        except (PluginPackageError, OSError, ValueError) as error:
            QtWidgets.QMessageBox.critical(self, "卸载插件失败", str(error))
            return
        self.refresh_plugins()
        QtWidgets.QMessageBox.information(self, "卸载成功", f"插件 {name} 已卸载。\n工具目录已同步更新。")

    def _on_clean_workspace(self):
        qdate = self.date_picker.date()
        target_date = date(qdate.year(), qdate.month(), qdate.day())
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认清理工作区",
            f"将永久清理本地时间 {target_date.isoformat()} 00:00 之前的任务工作区文件。\n"
            "历史审计记录不会被删除。是否继续？",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            cleaned_count = self.runtime.clean_workspace(target_date)
            QtWidgets.QMessageBox.information(self, "清理完成", f"已清理 {cleaned_count} 个过期工作区。")
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "清理失败", f"Runtime 清理过程中发生异常:\n{error}")


# ============================================================================
# 主窗口 (MainWindow)
# ============================================================================

class SettingsDialog(QtWidgets.QDialog):
    """只读展示 Runtime、工作区、插件目录、版本和环境诊断。"""

    def __init__(self, runtime: Runtime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.runtime_info = self.runtime.get_runtime_diagnostics()
        self.setWindowTitle("首选项与系统设置")
        self.setMinimumWidth(620)
        self._init_ui()

    def _open_folder(self, target_path: Path):
        folder = target_path if target_path.is_dir() else target_path.parent
        if not folder.exists():
            QtWidgets.QMessageBox.warning(self, "目录不存在", f"当前路径不存在，未执行任何创建操作:\n{folder}")
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))

    def _add_path_row(self, layout, caption: str, path_key: str):
        path = Path(self.runtime_info.get(path_key) or self.runtime.root)
        row = QtWidgets.QHBoxLayout()
        info = QtWidgets.QVBoxLayout()
        label = QtWidgets.QLabel(caption)
        label.setStyleSheet("font-weight: 600;")
        value = QtWidgets.QLabel(str(path))
        value.setObjectName("mutedText")
        value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setWordWrap(True)
        info.addWidget(label)
        info.addWidget(value)
        row.addLayout(info, 1)
        button = QtWidgets.QPushButton("📂 打开位置")
        button.setObjectName("secondaryButton")
        button.clicked.connect(lambda: self._open_folder(path))
        row.addWidget(button)
        layout.addLayout(row)

    def _init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QtWidgets.QLabel("⚙️ 首选项与设置")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)
        desc = QtWidgets.QLabel("只读查看 Runtime 路径、数据持久化位置和环境诊断；本阶段不修改配置。")
        desc.setObjectName("mutedText")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        paths_group = QtWidgets.QGroupBox("Runtime 与存储路径")
        paths_layout = QtWidgets.QVBoxLayout(paths_group)
        paths_layout.setContentsMargins(14, 14, 14, 14)
        paths_layout.setSpacing(12)
        self._add_path_row(paths_layout, "Runtime 根路径:", "runtime_root")
        self._add_path_row(paths_layout, "任务工作区目录 (Workspace):", "workspace_dir")
        self._add_path_row(paths_layout, "任务历史数据库位置:", "history_path")
        self._add_path_row(paths_layout, "用户插件目录 (Plugins):", "plugins_dir")
        self._add_path_row(paths_layout, "内置插件目录:", "bundled_plugins_dir")
        layout.addWidget(paths_group)

        sys_group = QtWidgets.QGroupBox("版本与只读运行诊断")
        sys_layout = QtWidgets.QFormLayout(sys_group)
        sys_layout.setContentsMargins(14, 14, 14, 14)
        sys_layout.setSpacing(8)
        sys_layout.addRow("TestBox 版本:", QtWidgets.QLabel(str(self.runtime_info.get("version", "-"))))
        sys_layout.addRow("操作系统:", QtWidgets.QLabel(sys.platform))
        sys_layout.addRow("Python 环境:", QtWidgets.QLabel(sys.version.split()[0]))
        sys_layout.addRow("Host 协议:", QtWidgets.QLabel(str(self.runtime_info.get("host_protocol", "-"))))
        boundary = QtWidgets.QLabel(str(self.runtime_info.get("plugin_host_boundary", "-")))
        boundary.setWordWrap(True)
        sys_layout.addRow("Plugin Host 边界:", boundary)
        layout.addWidget(sys_group)

        btn_box = QtWidgets.QHBoxLayout()
        btn_box.addStretch()
        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.setObjectName("primaryButton")
        btn_close.clicked.connect(self.accept)
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)


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
        self._interactive_task = False
        self._interactive_restore_geometry = None
        self.setWindowTitle("TestBox - 测试效能工具箱")
        self.setWindowIcon(get_app_logo_icon())
        # 初始尺寸根据屏幕可用区域裁剪；最小尺寸也不能大于常见小屏幕，
        # 让用户缩放窗口时由页面滚动区承接内容，而不是把右侧直接裁掉。
        self.setMinimumSize(760, 520)
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(1120, max(760, available.width() - 40))
            height = min(760, max(520, available.height() - 80))
        else:
            width, height = 1120, 760
        self.resize(width, height)

        self._init_ui()

    def _init_ui(self):
        root_widget = QtWidgets.QWidget()
        self.setCentralWidget(root_widget)

        root_layout = QtWidgets.QHBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. 侧边导航栏 (Sidebar)
        sidebar = QtWidgets.QWidget()
        self.sidebar = sidebar
        sidebar.setObjectName("navSidebar")
        # 窄窗口时允许导航栏从 220px 收缩到 176px，优先为主内容区保留空间。
        sidebar.setMinimumWidth(176)
        sidebar.setMaximumWidth(220)
        sidebar.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 18, 12, 18)
        sidebar_layout.setSpacing(8)

        # Logo / Brand
        brand_bar = QtWidgets.QHBoxLayout()
        brand_bar.setSpacing(10)
        brand_icon = QtWidgets.QLabel()
        brand_icon.setFixedSize(28, 28)
        brand_icon.setPixmap(get_app_logo_pixmap(28))
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

        # 底部设置按钮
        settings_btn = QtWidgets.QPushButton("⚙️  首选项与设置")
        settings_btn.setObjectName("secondaryButton")
        settings_btn.setToolTip("查看数据持久化目录、SQLite 任务库路径及常用配置")
        settings_btn.clicked.connect(self._open_settings_dialog)
        sidebar_layout.addWidget(settings_btn)

        sidebar_layout.addSpacing(6)

        # 底部版本信息
        runtime_version = self.runtime.get_runtime_diagnostics().get("version", "-")
        footer_lbl = QtWidgets.QLabel(f"TestBox Engine v{runtime_version}\nLocal-First Desktop")
        footer_lbl.setStyleSheet("color: #64748b; font-size: 11px; line-height: 1.4;")
        sidebar_layout.addWidget(footer_lbl)

        root_layout.addWidget(sidebar)

        # 2. 页面容器 (StackedWidget)
        self.stack = QtWidgets.QStackedWidget()
        self.stack.setObjectName("mainStack")

        # 实例化子页面
        self.page_catalog = ToolCatalogView(self.runtime)
        self.page_form = CommandDetailFormView(self.runtime, runtime_root=self._runtime_root)
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
        self.page_result.sqlSelectRequested.connect(self.navigate_to_command)
        self.page_result.openAnnotation.connect(self.open_annotation_dialog)

        self.page_history.taskSelected.connect(self.navigate_to_task_result)
        self.page_history.reExecuteRequested.connect(self.navigate_to_command)

        # 默认高亮并打开工具目录
        self.nav_list.setCurrentRow(0)

    def _add_nav_item(self, text: str, page_index: int):
        item = QtWidgets.QListWidgetItem(text)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, page_index)
        self.nav_list.addItem(item)

    def _open_settings_dialog(self):
        dlg = SettingsDialog(self.runtime, self)
        dlg.exec()

    def _on_nav_selected(self, row: int):
        item = self.nav_list.item(row)
        if item:
            page_index = item.data(QtCore.Qt.ItemDataRole.UserRole)
            self.switch_page(page_index)

    def _select_nav_page(self, page_index: int):
        """Keep the sidebar selection aligned with the active task context."""
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            if item and item.data(QtCore.Qt.ItemDataRole.UserRole) == page_index:
                blocker = QtCore.QSignalBlocker(self.nav_list)
                self.nav_list.setCurrentRow(row)
                del blocker
                return

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
        self._select_nav_page(4)
        self.page_result.display_task(task_id)
        self.stack.setCurrentIndex(3)

    def execute_task(self, command_name: str, params: dict):
        self._interactive_task = command_name == "evidence.build" and bool(params.get("interactive", False))
        if self._interactive_task:
            # 交互截图由插件 Host 在独立进程中执行；先隐藏 TestBox 主窗口，
            # 避免主窗口被截图，并在任务结束/异常后恢复。
            self._interactive_restore_geometry = self.saveGeometry()
            self.hide()
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

    def _restore_after_interactive(self):
        if not self._interactive_task:
            return
        geometry = self._interactive_restore_geometry
        self._interactive_task = False
        self._interactive_restore_geometry = None
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_task_finished(self, task_id: str, result: Any, elapsed: float):
        self._restore_after_interactive()
        self._select_nav_page(4)
        # The worker result is only a completion signal. Detail rendering reads
        # the persisted task and result through the main Runtime facade.
        self.page_result.display_task(task_id, elapsed=elapsed)
        self.stack.setCurrentIndex(3)

    def _on_task_failed(self, command: str, error_msg: str, elapsed: float):
        self._restore_after_interactive()
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
    color: #F8FAFC;
}

/* 所有 Qt 原生弹窗也使用深色高对比度主题，避免白底灰字。
   文件选择器通过 DontUseNativeDialog 使用此 QSS；消息框/输入框直接匹配。 */
QDialog, QMessageBox, QInputDialog, QFileDialog {
    background-color: #111318;
    color: #F8FAFC;
}
QDialog QLabel, QMessageBox QLabel, QInputDialog QLabel, QFileDialog QLabel {
    color: #F8FAFC;
}
QDialogButtonBox QPushButton, QMessageBox QPushButton {
    min-width: 78px;
    min-height: 32px;
    padding: 0 14px;
    background-color: #232832;
    color: #F8FAFC;
    border: 1px solid #3B4554;
    border-radius: 6px;
}
QDialogButtonBox QPushButton:hover, QMessageBox QPushButton:hover {
    background-color: #303846;
    border-color: #10B981;
}
QMessageBox QPushButton {
    background-color: #166534;
    border-color: #22C55E;
}
QMessageBox QPushButton:default {
    background-color: #059669;
    color: #FFFFFF;
}
QInputDialog QLineEdit, QFileDialog QLineEdit, QFileDialog QComboBox {
    background-color: #0B0D11;
    color: #F8FAFC;
    border: 1px solid #3B4554;
}
QFileDialog QListView, QFileDialog QTreeView, QFileDialog QTableView {
    background-color: #0B0D11;
    color: #F8FAFC;
    alternate-background-color: #141922;
    selection-background-color: #14532D;
    selection-color: #FFFFFF;
}
QFileDialog QToolButton, QFileDialog QPushButton {
    color: #F8FAFC;
    background-color: #232832;
    border: 1px solid #3B4554;
    border-radius: 6px;
    padding: 5px 10px;
}
QFileDialog QHeaderView::section {
    background-color: #1A1F28;
    color: #E2E8F0;
}
QToolTip {
    background-color: #111827;
    color: #F8FAFC;
    border: 1px solid #475569;
    padding: 5px;
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
    color: #CBD5E1;
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
    color: #CBD5E1;
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
    background-color: #20242C;
    color: #CBD5E1;
    border: 1px solid #27272A;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 500;
}

#tagLabelDanger {
    background-color: rgba(239, 68, 68, 0.15);
    color: #F87171;
    border: 1px solid rgba(239, 68, 68, 0.3);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}

#tagLabelInfo {
    background-color: rgba(59, 130, 246, 0.15);
    color: #60A5FA;
    border: 1px solid rgba(59, 130, 246, 0.3);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
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
    color: #CBD5E1;
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
    color: #CBD5E1;
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
QTableWidget, QTableView {
    background-color: #0D0D10;
    border: 1px solid #222226;
    border-radius: 6px;
    gridline-color: #1A1A1E;
    selection-background-color: #18181B;
    selection-color: #10B981;
    color: #EDEDED;
}

QHeaderView {
    background-color: #121215;
    border: none;
}

QHeaderView::section {
    background-color: #121215;
    color: #A1A1AA;
    font-weight: 600;
    padding: 8px;
    border: none;
    border-bottom: 1px solid #27272A;
}

QTableCornerButton::section {
    background-color: #121215;
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
    translator = QtCore.QTranslator(app)
    translations_path = QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load("qtbase_zh_CN", translations_path):
        app.installTranslator(translator)
    app.setWindowIcon(get_app_logo_icon())
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
