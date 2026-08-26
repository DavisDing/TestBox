from __future__ import annotations

import csv
import json
import re
import zipfile
from collections import OrderedDict
from pathlib import Path
from xml.etree import ElementTree

from testbox.sdk import PluginError, Result

SUPPORTED_FORMATS = {"json", "csv", "xlsx"}
DIALECT_QUOTES = {
    "mysql": ("`", "`"),
    "postgresql": ('"', '"'),
    "sqlite": ('"', '"'),
    "sqlserver": ("[", "]"),
    "oracle": ('"', '"'),
}


def detect_format(path: Path, requested: str) -> str:
    if requested != "auto":
        if requested not in SUPPORTED_FORMATS:
            raise PluginError("INVALID_PARAMS", f"不支持的输入格式: {requested}")
        return requested
    suffix = path.suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_FORMATS:
        raise PluginError("INVALID_PARAMS", "无法根据文件扩展名识别格式，请指定 input_format")
    return suffix


def _xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    if cell.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{namespace}t"))
    value = cell.find(f"{namespace}v")
    raw = value.text if value is not None and value.text is not None else ""
    if cell.get("t") == "s" and raw.isdigit() and int(raw) < len(shared_strings):
        return shared_strings[int(raw)]
    return raw


def read_xlsx(path: Path) -> list[dict[str, str]]:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with zipfile.ZipFile(path) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.text or "" for node in item.findall(f".//{namespace}t")) for item in root.findall(f"{namespace}si")]
            sheet_name = next((name for name in archive.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml")), None)
            if not sheet_name:
                raise ValueError("XLSX 缺少工作表")
            root = ElementTree.fromstring(archive.read(sheet_name))
            rows = []
            for row in root.findall(f".//{namespace}row"):
                cells = {}
                for cell in row.findall(f"{namespace}c"):
                    ref = cell.get("r", "A1")
                    column = re.match(r"([A-Z]+)", ref)
                    if column:
                        cells[column.group(1)] = _xlsx_cell_value(cell, shared)
                rows.append(cells)
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError, ValueError) as error:
        raise PluginError("INPUT_INVALID", "无法读取字段清单 XLSX 文件") from error
    if not rows:
        return []
    columns = sorted({column for row in rows for column in row}, key=lambda value: (len(value), value))
    headers = [rows[0].get(column, "") for column in columns]
    return [{headers[index]: row.get(column, "") for index, column in enumerate(columns) if headers[index]} for row in rows[1:]]


def read_rows(path: Path, input_format: str) -> list[dict[str, object]]:
    fmt = detect_format(path, input_format)
    try:
        if fmt == "json":
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, list):
                raise ValueError("JSON 根节点必须是字段数组")
            rows = value
        elif fmt == "csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
        else:
            rows = read_xlsx(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error, ValueError) as error:
        if isinstance(error, PluginError):
            raise
        raise PluginError("INPUT_INVALID", "无法读取字段清单文件") from error
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise PluginError("INPUT_INVALID", "字段清单必须包含至少一行对象记录")
    return rows


def value(row: dict[str, object], key: str) -> str:
    return str(row.get(key, "") or "").strip()


def quote_identifier(identifier: str, dialect: str) -> str:
    if not identifier or any(ord(character) < 32 for character in identifier):
        raise PluginError("INPUT_INVALID", f"字段清单包含无法安全引用的标识符: {identifier}")
    if dialect == "auto":
        if not re.fullmatch(r"[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*", identifier):
            raise PluginError("INPUT_INVALID", f"auto 方言无法安全引用标识符: {identifier}")
        return identifier
    left, right = DIALECT_QUOTES[dialect]
    parts = identifier.split(".")
    if any(not part or left in part or right in part for part in parts):
        raise PluginError("INPUT_INVALID", f"字段清单包含无法安全引用的标识符: {identifier}")
    escaped = [part.replace(right, right + right) for part in parts]
    return ".".join(f"{left}{part}{right}" for part in escaped)


def render_select(rows: list[dict[str, object]], dialect: str, include_comments: bool) -> tuple[str, int]:
    grouped: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    for row in rows:
        table = value(row, "table")
        field = value(row, "field")
        if not table or not field:
            raise PluginError("INPUT_INVALID", "字段清单必须包含非空 table 和 field 列")
        grouped.setdefault(table, []).append((field, value(row, "comment")))
    statements = []
    for table, fields in grouped.items():
        select_fields = []
        for field, comment in fields:
            expression = quote_identifier(field, dialect)
            if include_comments and comment:
                expression += f" /* {comment.replace('*/', '* /')} */"
            select_fields.append(f"    {expression}")
        statements.append("SELECT\n" + ",\n".join(select_fields) + f"\nFROM {quote_identifier(table, dialect)};")
    return "\n\n".join(statements) + "\n", len(grouped)


class Plugin:
    def init(self, context):
        self.context = context

    def execute(self, command, params):
        if command != "sql.select":
            raise PluginError("INVALID_PARAMS", "不支持的命令")
        source = Path(params["input"])
        if not source.is_file():
            raise PluginError("INPUT_NOT_FOUND", "字段清单输入文件不存在")
        rows = read_rows(source, params.get("input_format", "auto"))
        dialect = params.get("dialect", "auto")
        if dialect not in {"auto", *DIALECT_QUOTES}:
            raise PluginError("INVALID_PARAMS", f"不支持的 SQL 方言: {dialect}")
        sql, table_count = render_select(rows, dialect, params.get("include_comments", False))
        name = f"{self.context.task.id}.sql"
        self.context.files.write_text(name, sql)
        self.context.logger.info(f"根据 {len(rows)} 个字段生成 {table_count} 条 SELECT 语句")
        return Result("success", f"已生成 {table_count} 条 SELECT 语句", {"field_count": len(rows), "table_count": table_count, "dialect": dialect, "output_file": name}, [name], [])

    def destroy(self):
        pass
