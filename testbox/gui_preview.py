"""Schema-declared asynchronous parsing preview; all parsing stays in Host."""
from __future__ import annotations
import json
from PySide6 import QtCore, QtWidgets
from testbox.core.runtime import Runtime


class PreviewSignals(QtCore.QObject):
    finished = QtCore.Signal(str, object)


class PreviewWorker(QtCore.QRunnable):
    def __init__(self, root, command, params):
        super().__init__()
        self.root, self.command, self.params = root, command, params
        self.signals = PreviewSignals()

    def run(self):
        try:
            runtime = Runtime(self.root)
            try:
                task, result = runtime.run(self.command, self.params)
                self.signals.finished.emit(task, result)
            finally:
                runtime.close()
        except Exception as error:
            self.signals.finished.emit("", {"error": str(error)})


class ParsingPreviewPanel(QtWidgets.QGroupBox):
    """Editors synchronize options back to the command's existing Schema form."""
    def __init__(self, runtime, root, form, metadata, parent=None):
        super().__init__("导入与解析预览（只读，可调整后重新解析）", parent)
        self.runtime, self.root, self.form, self.metadata = runtime, root, form, metadata
        self.worker = None
        self.editors = []
        self.last_task_id = ""
        self.revision = 0
        self.request_revision = -1
        self.syncing = False
        layout = QtWidgets.QVBoxLayout(self)
        help_label = QtWidgets.QLabel("先选择输入文件或填写 SQL，再调整解析配置。预览通过真实任务执行，不修改原文件；预览前 20 行不代表仅校验前 20 行。")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.sources = QtWidgets.QTabWidget()
        layout.addWidget(self.sources)
        try:
            initial = form.get_values()
        except ValueError:
            initial = {}
        for source in metadata.get("sources", []):
            pane = QtWidgets.QWidget()
            fields = QtWidgets.QFormLayout(pane)
            options = initial.get(source.get("options", ""), {}) or {}
            editors = {}
            formats = QtWidgets.QComboBox()
            formats.addItems(["auto", "csv", "tsv", "txt", "json", "jsonl", "ndjson", "xlsx", "xlsm", "sql"])
            formats.setCurrentText(options.get("format", "auto"))
            editors["format"] = formats
            fields.addRow("文件格式", formats)
            for key, label, placeholder in (("encoding", "编码", "utf-8-sig / gb18030"),
                    ("delimiter", "列分隔符", r"自动/格式默认；可填 , | || \t"),
                    ("record_separator", "记录换行/分隔符", r"auto / \n / \r\n / 自定义"),
                    ("quotechar", "引用符", '默认 "；可设空'),
                    ("sheet", "Excel 工作表", "留空使用活动工作表"),
                    ("json_path", "JSON 数据路径", "data.rows，留空使用根对象")):
                default = {"encoding":"utf-8-sig", "record_separator":"auto", "quotechar":'"'}.get(key, "")
                value = options.get(key, default)
                editor = QtWidgets.QLineEdit(str(value).replace("\r",r"\r").replace("\n",r"\n").replace("\t",r"\t"))
                editor.setPlaceholderText(placeholder)
                editors[key] = editor
                fields.addRow(label, editor)
            for key, label, default in (("header_row", "表头行（1起）", 1), ("start_row", "数据起始行（0为自动）", 0)):
                editor = QtWidgets.QSpinBox()
                editor.setRange(0 if key == "start_row" else 1, 1_000_000)
                editor.setValue(options.get(key, default))
                editors[key] = editor
                fields.addRow(label, editor)
            header = QtWidgets.QCheckBox("包含表头")
            header.setChecked(options.get("has_header", True))
            editors["has_header"] = header
            fields.addRow("表头模式", header)
            button = QtWidgets.QPushButton(f"解析预览：{source.get('label', '输入')}")
            button.clicked.connect(lambda _checked=False, s=source, e=editors: self.preview(s, e))
            fields.addRow(button)
            self.sources.addTab(pane, source.get("label", "输入"))
            self.editors.append((source, editors, button))
            for editor in editors.values():
                signal = editor.currentTextChanged if isinstance(editor, QtWidgets.QComboBox) else editor.textChanged if isinstance(editor, QtWidgets.QLineEdit) else editor.valueChanged if isinstance(editor, QtWidgets.QSpinBox) else editor.toggled
                signal.connect(self._changed)
        self.status = QtWidgets.QLabel("尚未预览")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QtWidgets.QTableWidget()
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(160)
        self.table.setMaximumHeight(300)
        layout.addWidget(self.table)
        self.raw = QtWidgets.QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setMaximumHeight(140)
        layout.addWidget(self.raw)
        for source, editors, _button in self.editors:
            if source.get("options") in form.fields:
                _kind, widget = form.fields[source["options"]]
                widget.textChanged.connect(lambda text, e=editors: self._sync_options(text, e))
        # Source/normalization changes also invalidate the previous preview.
        for _kind, widget in form.fields.values():
            target = getattr(widget, "line_edit", widget)
            for name in ("textChanged", "valueChanged", "currentTextChanged", "toggled"):
                signal = getattr(target, name, None)
                if signal is not None:
                    signal.connect(self._changed)
                    break

    def _sync_options(self, text, editors):
        if self.syncing:
            return
        try:
            options = json.loads(text) if text.strip() else {}
            if not isinstance(options, dict):
                return
        except ValueError:
            return
        for name, editor in editors.items():
            default = {"format":"auto", "encoding":"utf-8-sig", "record_separator":"auto",
                       "quotechar":'"', "header_row":1, "start_row":0, "has_header":True}.get(name, "")
            value = options.get(name, default)
            blocker = QtCore.QSignalBlocker(editor)
            if isinstance(editor, QtWidgets.QComboBox):
                editor.setCurrentText(str(value))
            elif isinstance(editor, QtWidgets.QLineEdit):
                editor.setText(str(value).replace("\r",r"\r").replace("\n",r"\n").replace("\t",r"\t"))
            elif isinstance(editor, QtWidgets.QSpinBox) and isinstance(value, int):
                editor.setValue(value)
            elif isinstance(editor, QtWidgets.QCheckBox):
                editor.setChecked(bool(value))
            del blocker

    def _changed(self, *_args):
        self.revision += 1
        self.status.setText("配置或输入已改变，旧预览已失效，请重新预览")
        self.table.setRowCount(0)
        self.raw.clear()

    def apply_options(self):
        values = self.form.get_values()
        updates = {}
        for source, editors, _button in self.editors:
            key = source.get("options")
            if not key:
                continue
            options = dict(values.get(key) or {})
            direct_text = bool(values.get(source.get("text", ""))) and not values.get(source.get("input", ""))
            for name, editor in editors.items():
                if direct_text and name != "format":
                    continue
                value = editor.currentText() if isinstance(editor, QtWidgets.QComboBox) else editor.text() if isinstance(editor, QtWidgets.QLineEdit) else editor.value() if isinstance(editor, QtWidgets.QSpinBox) else editor.isChecked()
                if name in {"delimiter", "sheet", "json_path"} and value == "":
                    options.pop(name, None)
                else:
                    options[name] = value
            updates[key] = options
        self.syncing = True
        try:
            self.form.set_values(updates)
        finally:
            self.syncing = False
        return self.form.get_values()

    def preview(self, source, editors):
        if self.worker is not None:
            return
        try:
            values = self.apply_options()
            params = {"options": values.get(source.get("options", ""), {}), "sample_rows":20}
            if values.get(source.get("input", "")):
                params["input"] = values[source["input"]]
            if values.get(source.get("text", "")):
                params["text"] = values[source["text"]]
            if source.get("normalize"):
                params["normalize"] = values.get(source["normalize"], {})
            if source.get("mode"):
                params["input_mode"] = values.get(source["mode"], "sql")
            if "sql_column" in values:
                params["sql_column"] = values["sql_column"]
            command = self.metadata["command"]
            params = self.runtime.validate_params(command, params)
            self.worker = PreviewWorker(self.root, command, params)
            self.request_revision = self.revision
            self.worker.signals.finished.connect(self._finished)
            for _s, _e, button in self.editors:
                button.setEnabled(False)
            self.status.setText("正在解析完整输入，等待 Host 返回…")
            QtCore.QThreadPool.globalInstance().start(self.worker)
        except Exception as error:
            self.status.setText(f"预览未启动：{error}")

    @QtCore.Slot(str, object)
    def _finished(self, task, result):
        self.worker = None
        for _s, _e, button in self.editors:
            button.setEnabled(True)
        if self.request_revision != self.revision:
            self.status.setText("输入或配置在解析期间已改变，本次预览已失效（任务已记录），请重新预览")
            return
        self.last_task_id = task
        if isinstance(result, dict) or result.status != "success":
            message = result.get("error") if isinstance(result, dict) else result.message
            self.status.setText(f"解析失败：{message}；任务 {task or '未创建'}")
            return
        data = result.data
        columns, rows = data.get("columns", []), data.get("rows", [])
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([str(column) for column in columns])
        self.table.setRowCount(min(len(rows), 100))
        for index, row in enumerate(rows[:100]):
            for column, key in enumerate(columns):
                value = row.get(key, "〈字段缺失〉") if isinstance(row, dict) else row[column]
                text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
                self.table.setItem(index, column, QtWidgets.QTableWidgetItem(text[:2000]))
        self.table.resizeColumnsToContents()
        for index in range(self.table.columnCount()):
            self.table.setColumnWidth(index, min(self.table.columnWidth(index), 320))
        warnings = "；".join(result.warnings)
        self.status.setText(f"{result.message}；任务 {task}" + (f"；注意：{warnings}" if warnings else ""))
        self.raw.setPlainText(json.dumps({"raw_rows":data.get("raw_rows", []), "locations":data.get("locations", []),
                                         "complete":data.get("complete"), "sample_truncated":data.get("sample_truncated")}, ensure_ascii=False, indent=2)[:20000])
