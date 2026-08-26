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
TYPE_RE = re.compile(r"^(?P<type>(?:DOUBLE\s+PRECISION|CHARACTER\s+VARYING|TIMESTAMP(?:\s+WITH(?:OUT)?\s+TIME\s+ZONE)?|TIME(?:\s+WITH(?:OUT)?\s+TIME\s+ZONE)?|NATIONAL\s+CHARACTER(?:\s+VARYING)?|[A-Za-z][A-Za-z0-9_]*)(?:\s*\([^)]*\))?(?:\s*\[\])?)(?P<tail>.*)$", re.I | re.S)
TABLE_CONSTRAINT_RE = re.compile(rf"^(?:CONSTRAINT\s+(?P<name>{IDENTIFIER})\s+)?(?P<kind>PRIMARY\s+KEY|UNIQUE(?:\s+(?:KEY|INDEX))?|FOREIGN\s+KEY)(?:\s+{IDENTIFIER})?\s*\((?P<fields>[^)]*)\)(?:\s+REFERENCES\s+(?P<ref_table>{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})?)\s*\((?P<ref_fields>[^)]*)\))?", re.I | re.S)

DIALECT_SIGNALS = {
    "hudi": ((r"\bUSING\s+HUDI\b", 10), (r"['\"]hoodie\.", 8), (r"['\"]preCombineField['\"]", 8)),
    "maxcompute": ((r"\bLIFECYCLE\s+\d+\b", 9), (r"\bTBLPROPERTIES\s*\([^)]*odps\.", 8)),
    "hbase": ((r"\bSALT_BUCKETS\s*=", 9), (r"\bCOLUMN_ENCODED_BYTES\s*=", 9), (r"\bUNSIGNED_(?:DATE|TIME|TIMESTAMP|INT|LONG)\b", 6)),
    "hive": ((r"\bSTORED\s+AS\b", 8), (r"\bROW\s+FORMAT\b", 8), (r"\bSERDE\b", 7), (r"\bCLUSTERED\s+BY\b", 6)),
    "mysql": ((r"\bAUTO_INCREMENT\b", 8), (r"\bENGINE\s*=", 8), (r"\bUNSIGNED\b", 4), (r"\bENUM\s*\(", 4), (r"`[^`]+`", 2)),
    "postgresql": ((r"\b(?:BIG|SMALL)?SERIAL\b", 8), (r"::[A-Za-z_]", 6), (r"\bJSONB\b", 5), (r"\bTIMESTAMPTZ\b", 5), (r"\bARRAY\s*\[", 3)),
    "sqlserver": ((r"\bIDENTITY\s*\(", 9), (r"\bUNIQUEIDENTIFIER\b", 7), (r"\bDATETIME2\b", 6), (r"\[[^]]+\]", 2), (r"(?:^|\n)\s*GO\s*(?:$|\n)", 5)),
    "oracle": ((r"\bVARCHAR2\b", 8), (r"\bNVARCHAR2\b", 8), (r"\bSYSTIMESTAMP\b", 7), (r"\bNUMBER\s*\(", 4), (r"\bGENERATED\s+BY\s+DEFAULT\s+AS\s+IDENTITY\b", 5)),
    "sqlite": ((r"\bAUTOINCREMENT\b", 9), (r"\bWITHOUT\s+ROWID\b", 8), (r"\bPRAGMA\b", 7)),
}


def clean_identifier(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and ((value[0], value[-1]) in {("`", "`"), ('"', '"'), ("[", "]")}):
        return value[1:-1]
    return value


def clean_qualified_identifier(value: str) -> str:
    return ".".join(clean_identifier(part) for part in split_quoted(value, "."))


def unquote(value: str) -> str:
    return value.replace("''", "'").strip()


def detect_dialect(sql: str) -> str:
    scores = {
        dialect: sum(weight for pattern, weight in signals if re.search(pattern, sql, re.I | re.S))
        for dialect, signals in DIALECT_SIGNALS.items()
    }
    dialect, score = max(scores.items(), key=lambda item: item[1])
    return dialect if score else "auto"


def split_quoted(value: str, delimiter: str = ",") -> list[str]:
    result, current, quote = [], [], None
    pairs = {"[": "]", "`": "`", '"': '"', "'": "'"}
    index = 0
    while index < len(value):
        character = value[index]
        if quote:
            current.append(character)
            if character == quote:
                if index + 1 < len(value) and value[index + 1] == quote and quote != "]":
                    current.append(value[index + 1]); index += 1
                else:
                    quote = None
        elif character in pairs:
            quote = pairs[character]; current.append(character)
        elif character == delimiter:
            result.append("".join(current).strip()); current = []
        else:
            current.append(character)
        index += 1
    if current or value.endswith(delimiter): result.append("".join(current).strip())
    return result


def strip_comments(sql: str) -> str:
    result, quote, index = [], None, 0
    quote_pairs = {"'": "'", '"': '"', "`": "`", "[": "]"}
    while index < len(sql):
        if quote:
            result.append(sql[index])
            if sql[index] == quote:
                if quote != "]" and index + 1 < len(sql) and sql[index + 1] == quote:
                    result.append(sql[index + 1]); index += 1
                else:
                    quote = None
        elif sql[index] in quote_pairs:
            quote = quote_pairs[sql[index]]; result.append(sql[index])
        elif sql.startswith("--", index):
            end = sql.find("\n", index)
            index = len(sql) - 1 if end < 0 else end - 1
            result.append(" ")
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            index = len(sql) - 1 if end < 0 else end + 1
            result.append(" ")
        else:
            result.append(sql[index])
        index += 1
    return "".join(result)


def extract_tables(sql: str) -> list[tuple[str, str, str]]:
    result = []
    quote_pairs = {"'": "'", '"': '"', "`": "`", "[": "]"}
    for match in CREATE_RE.finditer(sql):
        depth, quote, close_index, index = 1, None, None, match.end()
        while index < len(sql):
            character = sql[index]
            if quote and character == quote:
                if quote != "]" and index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2; continue
                quote = None
            elif not quote and character in quote_pairs:
                quote = quote_pairs[character]
            elif not quote:
                if character == "(": depth += 1
                elif character == ")": depth -= 1
                if depth == 0:
                    close_index = index
                    break
            index += 1
        if close_index is not None:
            statement_end = close_index + 1
            quote = None
            while statement_end < len(sql):
                character = sql[statement_end]
                if quote and character == quote:
                    if quote != "]" and statement_end + 1 < len(sql) and sql[statement_end + 1] == quote:
                        statement_end += 1
                    else: quote = None
                elif not quote and character in quote_pairs: quote = quote_pairs[character]
                elif character == ";" and not quote: break
                statement_end += 1
            tail = sql[close_index + 1:statement_end]
            result.append((clean_qualified_identifier(match.group("table")), sql[match.end():close_index], tail))
    return result


def split_fields(body: str) -> list[str]:
    result, current, depth, angle_depth, quote, index = [], [], 0, 0, None, 0
    quote_pairs = {"'": "'", '"': '"', "`": "`", "[": "]"}
    while index < len(body):
        character = body[index]
        if quote and character == quote:
            if quote != "]" and index + 1 < len(body) and body[index + 1] == quote:
                current.extend((character, body[index + 1])); index += 2; continue
            quote = None
        elif not quote and character in quote_pairs:
            quote = quote_pairs[character]
        elif not quote:
            depth += character == "("
            depth -= character == ")"
            if character == "<" and (angle_depth or re.search(r"(?:ARRAY|MAP|STRUCT)\s*$", "".join(current), re.I)):
                angle_depth += 1
            elif character == ">" and angle_depth:
                angle_depth -= 1
        if character == "," and depth == 0 and angle_depth == 0 and not quote:
            result.append("".join(current)); current = []
        else:
            current.append(character)
        index += 1
    if current: result.append("".join(current))
    return result


def field_name_and_rest(definition: str) -> tuple[str, str] | None:
    match = re.match(rf"^\s*(?P<field>{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})*)\s+(?P<rest>.+)$", definition, re.S)
    if not match: return None
    return clean_qualified_identifier(match.group("field")), match.group("rest").strip()


def parse_type(rest: str) -> tuple[str, str]:
    match = TYPE_RE.match(rest)
    if not match: return rest.split(None, 1)[0].upper(), rest
    type_name = re.sub(r"\s+", " ", match.group("type")).strip().upper()
    tail = match.group("tail")
    if tail.lstrip().startswith("<"):
        leading = len(tail) - len(tail.lstrip())
        depth, quote, index = 0, None, leading
        while index < len(tail):
            character = tail[index]
            if quote and character == quote:
                quote = None
            elif not quote and character in {"'", '"', "`"}:
                quote = character
            elif not quote:
                if character == "<": depth += 1
                elif character == ">":
                    depth -= 1
                    if depth == 0:
                        type_name += re.sub(r"\s+", " ", tail[leading:index + 1]).upper()
                        tail = tail[index + 1:]
                        break
            index += 1
    if re.match(r"^\s+UNSIGNED\b", tail, re.I):
        type_name += " UNSIGNED"
        tail = re.sub(r"^\s+UNSIGNED\b", "", tail, count=1, flags=re.I)
    return type_name, tail.strip()


def type_dimensions(type_name: str) -> tuple[int | None, int | None, int | None]:
    match = re.search(r"\((\d+)\s*(?:,\s*(\d+))?\s*(?:CHAR|BYTE)?\)", type_name, re.I)
    if not match: return None, None, None
    first, second = int(match.group(1)), match.group(2)
    numeric = type_name.startswith(("DECIMAL", "NUMERIC", "NUMBER"))
    return (None, first, int(second) if second is not None else None) if numeric else (first, None, None)


def parse_inline(field: str, rest: str, table: str, table_comment: str, dialect: str) -> dict[str, object]:
    type_name, tail = parse_type(rest)
    length, precision, scale = type_dimensions(type_name)
    default_match = re.search(r"\bDEFAULT\s+(.+?)(?=\s+(?:NOT\s+NULL|NULL|PRIMARY\s+KEY|UNIQUE|REFERENCES|COMMENT|CHECK|CONSTRAINT|ON\s+UPDATE)\b|$)", tail, re.I | re.S)
    comment_match = re.search(r"\bCOMMENT\s+(['\"])(.*?)\1", tail, re.I | re.S)
    reference_match = re.search(r"\bREFERENCES\s+([^\s(]+)\s*\(([^)]*)\)", tail, re.I)
    return {
        "table": table,
        "field": field,
        "type": type_name,
        "length": length,
        "precision": precision,
        "scale": scale,
        "nullable": not bool(re.search(r"\bNOT\s+NULL\b", tail, re.I)),
        "primary_key": bool(re.search(r"\bPRIMARY\s+KEY\b", tail, re.I)),
        "unique": bool(re.search(r"\bUNIQUE\b", tail, re.I)),
        "auto_increment": type_name in {"SERIAL", "BIGSERIAL", "SMALLSERIAL"} or bool(re.search(r"\b(?:AUTO_INCREMENT|AUTOINCREMENT|IDENTITY|GENERATED\s+(?:ALWAYS|BY\s+DEFAULT)\s+AS\s+IDENTITY)\b", tail, re.I)),
        "default": default_match.group(1).strip() if default_match else "",
        "comment": unquote(comment_match.group(2)) if comment_match else "",
        "foreign_table": clean_qualified_identifier(reference_match.group(1)) if reference_match else "",
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
        partition_match = re.search(r"\bPARTITION(?:ED)?\s+BY\s+(?P<partition>\([^)]*\)|.+?)(?=\s+(?:PARTITIONS?\b|SUBPARTITION\s+BY\b|STORED\b|ROW\s+FORMAT\b|TBLPROPERTIES\b|LIFECYCLE\b)|$)", tail, re.I | re.S)
        partition = re.sub(r"\s+", " ", partition_match.group("partition")).strip() if partition_match else ""
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
            row["partition"] = partition
            row["comment"] = column_comments.get((table, field), row["comment"])
            rows.append(row)
        by_field = {row["field"]: row for row in rows if row["table"] == table}
        for constraint in table_constraints:
            fields = [clean_identifier(item) for item in split_quoted(constraint["fields"])]
            kind = re.sub(r"\s+", " ", constraint["kind"].upper())
            for field in fields:
                if field not in by_field: continue
                if kind == "PRIMARY KEY": by_field[field]["primary_key"] = True
                elif kind.startswith("UNIQUE"): by_field[field]["unique"] = True
                elif kind == "FOREIGN KEY":
                    by_field[field]["foreign_table"] = clean_qualified_identifier(constraint.get("ref_table") or "")
                    ref_fields = [clean_identifier(item) for item in split_quoted(constraint.get("ref_fields") or "")]
                    field_index = fields.index(field)
                    by_field[field]["foreign_field"] = ref_fields[field_index] if field_index < len(ref_fields) else ""
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
        requested_dialect = params.get("dialect", "auto")
        dialect = detect_dialect(sql) if requested_dialect == "auto" else "maxcompute" if requested_dialect == "mc" else requested_dialect
        rows, warnings = parse_sql(sql, dialect)
        if not rows: raise PluginError("INPUT_INVALID", "未解析到字段定义")
        if warnings and params.get("fail_on_unsupported"): raise PluginError("INPUT_INVALID", warnings[0], details={"warnings": warnings})
        if not params.get("include_constraints", True):
            constraint_fields = {"primary_key", "unique", "foreign_table", "foreign_field"}
            rows = [{key: value for key, value in row.items() if key not in constraint_fields} for row in rows]
        name = f"{self.context.task.id}.{params['format']}"
        if params["format"] == "json": self.context.files.write_text(name, json.dumps(rows, ensure_ascii=False, indent=2))
        elif params["format"] == "csv":
            buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows); self.context.files.write_text(name, buffer.getvalue())
        else: self.context.files.write_bytes(name, xlsx(rows))
        self.context.logger.info(f"解析 {len(rows)} 个字段，产生 {len(warnings)} 条警告")
        return Result("success", f"已解析 {len(rows)} 个字段", {"field_count": len(rows), "requested_dialect": requested_dialect, "dialect": dialect, "format": params["format"], "output_file": name, "warnings": warnings, "tables": sorted({row["table"] for row in rows})}, [name], warnings)

    def destroy(self): pass
