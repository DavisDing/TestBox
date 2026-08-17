from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from testbox.sdk import PluginError, Result

IDENTIFIER = r"(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][\w$]*)"
CREATE_RE = re.compile(rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<table>{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})?)\s*\(", re.I)
COMMENT_TABLE_RE = re.compile(rf"COMMENT\s+ON\s+TABLE\s+(?P<table>{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})?)\s+IS\s+'(?P<comment>(?:''|[^'])*)'", re.I)
COMMENT_COLUMN_RE = re.compile(rf"COMMENT\s+ON\s+COLUMN\s+(?P<table>{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})?)\s*\.\s*(?P<field>{IDENTIFIER})\s+IS\s+'(?P<comment>(?:''|[^'])*)'", re.I)
TYPE_RE = re.compile(r"^(?P<type>(?:DOUBLE\s+PRECISION|CHARACTER\s+VARYING|TIMESTAMP(?:\s+WITH(?:OUT)?\s+TIME\s+ZONE)?|TIME(?:\s+WITH(?:OUT)?\s+TIME\s+ZONE)?|NATIONAL\s+CHARACTER(?:\s+VARYING)?|[A-Za-z]+)(?:\s*\([^)]*\))?(?:\s*\[\])?)(?P<tail>.*)$", re.I | re.S)
TABLE_CONSTRAINT_RE = re.compile(r"^(?:CONSTRAINT\s+(?P<name>\S+)\s+)?(?P<kind>PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY)\s*\((?P<fields>[^)]*)\)(?:\s+REFERENCES\s+(?P<ref_table>[^ (]+)\s*\((?P<ref_fields>[^)]*)\))?", re.I | re.S)


def clean_identifier(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and ((value[0], value[-1]) in {("`", "`"), ('"', '"'), ("[", "]")}):
        return value[1:-1]
    return value


def clean_qualified_identifier(value: str) -> str:
    return ".".join(clean_identifier(part.strip()) for part in value.split("."))


def unquote(value: str) -> str:
    return value.replace("''", "'").strip()


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\r\n]*", " ", sql)


def extract_tables(sql: str) -> list[tuple[str, str, str]]:
    result = []
    for match in CREATE_RE.finditer(sql):
        depth, quote = 1, None
        for index, character in enumerate(sql[match.end():], match.end()):
            if character in "'\"" and (index == 0 or sql[index - 1] != "\\"):
                quote = None if quote == character else character if quote is None else quote
            elif not quote:
                if character == "(": depth += 1
                elif character == ")": depth -= 1
                if depth == 0:
                    tail = sql[index + 1: sql.find(";", index) if sql.find(";", index) >= 0 else len(sql)]
                    result.append((clean_qualified_identifier(match.group("table")), sql[match.end():index], tail))
                    break
    return result


def split_fields(body: str) -> list[str]:
    result, current, depth, quote = [], [], 0, None
    for character in body:
        if character in "'\"`" and (not current or current[-1] != "\\"):
            quote = None if quote == character else character if quote is None else quote
        elif not quote:
            depth += character == "("
            depth -= character == ")"
        if character == "," and depth == 0 and not quote:
            result.append("".join(current)); current = []
        else:
            current.append(character)
    if current: result.append("".join(current))
    return result


def field_name_and_rest(definition: str) -> tuple[str, str] | None:
    match = re.match(rf"^\s*(?P<field>{IDENTIFIER})\s+(?P<rest>.+)$", definition, re.S)
    if not match: return None
    return clean_identifier(match.group("field")), match.group("rest").strip()


def parse_type(rest: str) -> tuple[str, str]:
    match = TYPE_RE.match(rest)
    if not match: return rest.split(None, 1)[0].upper(), rest
    type_name = re.sub(r"\s+", " ", match.group("type")).strip().upper()
    tail = match.group("tail")
    if re.match(r"^\s+UNSIGNED\b", tail, re.I):
        type_name += " UNSIGNED"
        tail = re.sub(r"^\s+UNSIGNED\b", "", tail, count=1, flags=re.I)
    return type_name, tail.strip()


def parse_inline(field: str, rest: str, table: str, table_comment: str, dialect: str) -> dict[str, object]:
    type_name, tail = parse_type(rest)
    default_match = re.search(r"\bDEFAULT\s+(.+?)(?=\s+(?:NOT\s+NULL|NULL|PRIMARY\s+KEY|UNIQUE|REFERENCES|COMMENT|CHECK|CONSTRAINT)\b|$)", tail, re.I | re.S)
    comment_match = re.search(r"\bCOMMENT\s+(['\"])(.*?)\1", tail, re.I | re.S)
    reference_match = re.search(r"\bREFERENCES\s+([^\s(]+)\s*\(([^)]*)\)", tail, re.I)
    return {
        "table": table,
        "field": field,
        "type": type_name,
        "nullable": not bool(re.search(r"\bNOT\s+NULL\b", tail, re.I)),
        "primary_key": bool(re.search(r"\bPRIMARY\s+KEY\b", tail, re.I)),
        "unique": bool(re.search(r"\bUNIQUE\b", tail, re.I)),
        "auto_increment": bool(re.search(r"\b(?:AUTO_INCREMENT|IDENTITY|GENERATED\s+ALWAYS\s+AS\s+IDENTITY)\b", tail, re.I)),
        "default": default_match.group(1).strip() if default_match else "",
        "comment": unquote(comment_match.group(2)) if comment_match else "",
        "foreign_table": clean_identifier(reference_match.group(1)) if reference_match else "",
        "foreign_field": clean_identifier(reference_match.group(2)) if reference_match else "",
        "table_comment": table_comment,
        "dialect": dialect,
    }


def parse_sql(sql: str, dialect: str) -> tuple[list[dict[str, object]], list[str]]:
    sql = strip_comments(sql)
    tables = extract_tables(sql)
    table_comments = {clean_qualified_identifier(item.group("table")): unquote(item.group("comment")) for item in COMMENT_TABLE_RE.finditer(sql)}
    column_comments = {(clean_qualified_identifier(item.group("table")), clean_identifier(item.group("field"))): unquote(item.group("comment")) for item in COMMENT_COLUMN_RE.finditer(sql)}
    rows, warnings = [], []
    for table, body, tail in tables:
        table_comment = table_comments.get(table, "")
        inline_comment = re.search(r"\bCOMMENT\s*(?:=|\s)\s*['\"]([^'\"]*)['\"]", tail, re.I)
        if inline_comment: table_comment = inline_comment.group(1)
        definitions = split_fields(body)
        table_constraints = []
        for definition in definitions:
            constraint = TABLE_CONSTRAINT_RE.match(definition.strip())
            if constraint:
                table_constraints.append(constraint.groupdict())
                continue
            parsed = field_name_and_rest(definition)
            if not parsed:
                warnings.append(f"{table}: 无法识别定义 `{definition.strip()[:80]}`")
                continue
            field, rest = parsed
            if field.upper() in {"PRIMARY", "KEY", "UNIQUE", "CONSTRAINT", "INDEX", "CHECK", "FOREIGN"}:
                warnings.append(f"{table}.{field}: 跳过未建模的约束")
                continue
            row = parse_inline(field, rest, table, table_comment, dialect)
            row["comment"] = column_comments.get((table, field), row["comment"])
            rows.append(row)
        by_field = {row["field"]: row for row in rows if row["table"] == table}
        for constraint in table_constraints:
            fields = [clean_identifier(item) for item in constraint["fields"].split(",")]
            kind = re.sub(r"\s+", " ", constraint["kind"].upper())
            for field in fields:
                if field not in by_field: continue
                if kind == "PRIMARY KEY": by_field[field]["primary_key"] = True
                elif kind == "UNIQUE": by_field[field]["unique"] = True
                elif kind == "FOREIGN KEY":
                    by_field[field]["foreign_table"] = clean_identifier(constraint.get("ref_table") or "")
                    ref_fields = [clean_identifier(item) for item in (constraint.get("ref_fields") or "").split(",")]
                    by_field[field]["foreign_field"] = ref_fields[fields.index(field)] if fields.index(field) < len(ref_fields) else ""
    return rows, warnings


def column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def xlsx(rows: list[dict[str, object]]) -> bytes:
    headers = list(rows[0]); values = [headers] + [[str(row.get(header, "")) for header in headers] for row in rows]
    sheet_rows = []
    for row_index, row in enumerate(values, 1):
        cells = "".join(f'<c r="{column_name(column_index + 1)}{row_index}" t="inlineStr"><is><t>{escape(value)}</t></is></c>' for column_index, value in enumerate(row))
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')
    sheet = '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(sheet_rows) + "</sheetData></worksheet>"
    files = {"[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>', "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>', "xl/workbook.xml": '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="字段清单" sheetId="1" r:id="rId1"/></sheets></workbook>', "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>', "xl/worksheets/sheet1.xml": sheet}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items(): archive.writestr(name, content)
    return output.getvalue()


class Plugin:
    def init(self, context): self.context = context

    def execute(self, command, params):
        if command != "sql.parse": raise PluginError("INVALID_PARAMS", "不支持的命令")
        source = Path(params["input"])
        if not source.is_file(): raise PluginError("INPUT_NOT_FOUND", "SQL 输入文件不存在")
        encoding = self.context.config.get("input_encoding", "utf-8")
        try: sql = source.read_text(encoding=encoding)
        except (LookupError, UnicodeDecodeError) as error: raise PluginError("INPUT_INVALID", f"无法按 {encoding} 读取 SQL 输入文件") from error
        dialect = params.get("dialect", "auto")
        rows, warnings = parse_sql(sql, dialect)
        if not rows: raise PluginError("INPUT_INVALID", "未解析到字段定义")
        if warnings and params.get("fail_on_unsupported"): raise PluginError("INPUT_INVALID", warnings[0], details={"warnings": warnings})
        name = {"json": "fields.json", "csv": "fields.csv", "xlsx": "fields.xlsx"}[params["format"]]
        if params["format"] == "json": self.context.files.write_text(name, json.dumps(rows, ensure_ascii=False, indent=2))
        elif params["format"] == "csv":
            buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows); self.context.files.write_text(name, buffer.getvalue())
        else: self.context.files.write_bytes(name, xlsx(rows))
        self.context.logger.info(f"解析 {len(rows)} 个字段，产生 {len(warnings)} 条警告")
        return Result("success", f"已解析 {len(rows)} 个字段", {"field_count": len(rows), "dialect": dialect, "warnings": warnings, "tables": sorted({row["table"] for row in rows})}, [name], warnings)

    def destroy(self): pass
