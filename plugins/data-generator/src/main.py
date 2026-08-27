from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from testbox.sdk import PluginError, Result

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨"
GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英"
MOBILE_PREFIXES = tuple(str(item) for section in (range(130, 140), range(145, 150), range(150, 160), (162, 165, 166, 167), range(170, 179), range(180, 190), range(191, 200)) for item in section)
IDENTIFIER = r'(?:`(?:``|[^`])+`|"(?:""|[^"])+"|\[(?:\]\]|[^\]])+\]|[A-Za-z_#$][\w$#]*)'
CREATE_START_RE = re.compile(
    rf'CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+|UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
    rf'(?P<table>{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})*)\s*\(',
    re.I,
)
TYPE_RE = re.compile(
    r'^(?P<type>(?:DOUBLE\s+PRECISION|CHARACTER\s+VARYING|'
    r'TIMESTAMP(?:\s+WITH(?:OUT)?\s+TIME\s+ZONE)?|'
    r'TIME(?:\s+WITH(?:OUT)?\s+TIME\s+ZONE)?|'
    r'NATIONAL\s+CHARACTER(?:\s+VARYING)?|'
    r'[A-Za-z][A-Za-z0-9_]*)(?:\s*\([^)]*\))?(?:\s*\[\])?)'
    r'(?P<tail>.*)$',
    re.I | re.S,
)
TABLE_CONSTRAINT_RE = re.compile(
    rf'^\s*(?:CONSTRAINT\s+(?P<name>{IDENTIFIER})\s+)?'
    r'(?P<kind>PRIMARY\s+KEY|UNIQUE(?:\s+(?:KEY|INDEX))?|FOREIGN\s+KEY)\b'
    r'(?P<rest>.*)$',
    re.I | re.S,
)
FIELD_RE = re.compile(
    rf'^\s*(?P<field>{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})*)\s+'
    r'(?P<rest>.+)$',
    re.I | re.S,
)


def column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def xlsx(rows: list[dict[str, object]]) -> bytes:
    headers = list(rows[0])
    values = [headers] + [[str(row.get(header, "")) for header in headers] for row in rows]
    sheet_rows = []
    for row_index, row in enumerate(values, 1):
        cells = "".join(f'<c r="{column_name(column_index + 1)}{row_index}" t="inlineStr"><is><t>{escape(value)}</t></is></c>' for column_index, value in enumerate(row))
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')
    sheet = '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(sheet_rows) + "</sheetData></worksheet>"
    files = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="测试数据" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def _quote_pairs() -> dict[str, str]:
    return {"'": "'", '"': '"', "`": "`", "[": "]"}


def _unquote_identifier(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and ((value[0], value[-1]) in {("`", "`"), ('"', '"'), ("[", "]")}):
        closing = value[-1]
        inner = value[1:-1]
        if closing == "]":
            return inner.replace("]]", "]")
        return inner.replace(closing * 2, closing)
    return value


def _split_quoted(value: str, delimiter: str = ".") -> list[str]:
    parts, current, quote, index = [], [], None, 0
    pairs = _quote_pairs()
    while index < len(value):
        character = value[index]
        if quote:
            current.append(character)
            if character == quote:
                if quote != "]" and index + 1 < len(value) and value[index + 1] == quote:
                    current.append(value[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in pairs:
            quote = pairs[character]
            current.append(character)
        elif character == delimiter:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    if current or value.endswith(delimiter):
        parts.append("".join(current).strip())
    return parts


def _normalize_table_name(value: str) -> str:
    return ".".join(_unquote_identifier(part) for part in _split_quoted(value))


def _strip_sql_comments(content: str) -> str:
    result, quote, index = [], None, 0
    pairs = _quote_pairs()
    while index < len(content):
        character = content[index]
        if quote:
            result.append(character)
            if character == quote:
                if quote != "]" and index + 1 < len(content) and content[index + 1] == quote:
                    result.append(content[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in pairs:
            quote = pairs[character]
            result.append(character)
        elif content.startswith("--", index):
            end = content.find("\n", index + 2)
            result.append(" ")
            index = len(content) if end < 0 else end
            continue
        elif content.startswith("/*", index):
            end = content.find("*/", index + 2)
            result.append(" ")
            index = len(content) if end < 0 else end + 2
            continue
        elif character == "#":
            line_start = not content[:index].strip() or content[:index].rstrip().endswith("\n")
            if line_start:
                end = content.find("\n", index + 1)
                result.append(" ")
                index = len(content) if end < 0 else end
                continue
            result.append(character)
        else:
            result.append(character)
        index += 1
    return "".join(result)


def _mask_sql_literals(content: str) -> str:
    """Mask single-quoted literals so CREATE TABLE text inside a literal is ignored."""
    result, index = list(content), 0
    while index < len(content):
        if content[index] != "'":
            index += 1
            continue
        result[index] = " "
        index += 1
        while index < len(content):
            result[index] = " "
            if content[index] == "'":
                if index + 1 < len(content) and content[index + 1] == "'":
                    result[index + 1] = " "
                    index += 2
                    continue
                index += 1
                break
            if content[index] == "\\" and index + 1 < len(content):
                result[index + 1] = " "
                index += 2
                continue
            index += 1
    return "".join(result)


def all_create_table_bodies(content: str) -> list[tuple[str, str]]:
    """Extract every CREATE TABLE body, including nested types and quoted identifiers."""
    cleaned = _strip_sql_comments(content)
    searchable = _mask_sql_literals(cleaned)
    tables: list[tuple[str, str]] = []
    for match in CREATE_START_RE.finditer(searchable):
        depth, quote, index = 1, None, match.end()
        while index < len(cleaned):
            character = cleaned[index]
            if quote:
                if character == quote:
                    if quote != "]" and index + 1 < len(cleaned) and cleaned[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
            elif character in _quote_pairs():
                quote = _quote_pairs()[character]
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    tables.append((_normalize_table_name(match.group("table")), cleaned[match.end():index]))
                    break
            index += 1
    return tables


def first_create_table_body(content: str) -> str | None:
    tables = all_create_table_bodies(content)
    return tables[0][1] if tables else None


def split_fields(body: str) -> list[str]:
    result, current, depth, angle_depth, quote, index = [], [], 0, 0, None, 0
    pairs = _quote_pairs()
    while index < len(body):
        character = body[index]
        if quote:
            current.append(character)
            if character == quote:
                if quote != "]" and index + 1 < len(body) and body[index + 1] == quote:
                    current.append(body[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in pairs:
            quote = pairs[character]
            current.append(character)
        else:
            depth += character == "("
            depth -= character == ")"
            if character == "<" and (angle_depth or re.search(r"(?:ARRAY|MAP|STRUCT)\s*$", "".join(current), re.I)):
                angle_depth += 1
            elif character == ">" and angle_depth:
                angle_depth -= 1
            current.append(character)
        if character == "," and depth == 0 and angle_depth == 0 and not quote:
            result.append("".join(current[:-1]).strip())
            current = []
        index += 1
    if current and "".join(current).strip():
        result.append("".join(current).strip())
    return result


def _identifier_list(value: str) -> list[str]:
    return [_normalize_table_name(item) for item in split_fields(value) if item.strip()]


def rule(name: str, generator: str, *, unique: bool = False, options: dict | None = None) -> dict:
    return {"name": name, "generator": generator, "enabled": True, "unique": unique, "options": options or {}}


def template_rules(template: str) -> list[dict]:
    templates = {
        "retail_customer": [rule("customer_id", "sequence", unique=True, options={"prefix": "TEST-CUST-", "width": 8}), rule("full_name", "name_cn"), rule("gender", "weighted_enum", options={"values": ["男", "女"]}), rule("mobile", "mobile_cn"), rule("registered_address", "china_address"), rule("customer_type", "weighted_enum", options={"values": [{"value": "个人客户", "weight": 80}, {"value": "小微企业", "weight": 15}, {"value": "对公客户", "weight": 5}]}), rule("risk_level", "weighted_enum", options={"values": [{"value": "低", "weight": 60}, {"value": "中", "weight": 30}, {"value": "高", "weight": 10}]}), rule("registered_at", "datetime_random", options={"start": "2020-01-01T00:00:00", "end": "2026-12-31T23:59:59"})],
        "account": [rule("account_id", "sequence", unique=True, options={"prefix": "TEST-ACCT-", "width": 12}), rule("customer_id", "sequence", options={"prefix": "TEST-CUST-", "width": 8}), rule("account_type", "weighted_enum", options={"values": ["活期", "定期", "信用"]}), rule("currency", "weighted_enum", options={"values": [{"value": "CNY", "weight": 95}, {"value": "USD", "weight": 3}, {"value": "HKD", "weight": 2}]}), rule("balance", "decimal_random", options={"min": 0, "max": 500000, "scale": 2}), rule("opened_at", "date_random", options={"start": "2020-01-01", "end": "2026-12-31"})],
        "product": [rule("product_id", "sequence", unique=True, options={"prefix": "TEST-PROD-", "width": 6}), rule("product_name", "template", options={"value": "测试产品-{index}"}), rule("product_type", "weighted_enum", options={"values": ["存款", "贷款", "理财"]}), rule("term_months", "weighted_enum", options={"values": [3, 6, 12, 24]}), rule("rate", "decimal_random", options={"min": 0.01, "max": 0.08, "scale": 4})],
        "transaction": [rule("transaction_id", "transaction_id", unique=True), rule("customer_id", "sequence", options={"prefix": "TEST-CUST-", "width": 8}), rule("account_id", "sequence", options={"prefix": "TEST-ACCT-", "width": 12}), rule("amount", "decimal_random", options={"min": 0.01, "max": 500000, "scale": 2}), rule("currency", "weighted_enum", options={"values": ["CNY", "USD", "HKD"]}), rule("transaction_time", "datetime_random", options={"start": "2026-01-01T00:00:00", "end": "2026-12-31T23:59:59"}), rule("channel", "weighted_enum", options={"values": ["柜面", "手机银行", "网银", "ATM"]}), rule("status", "weighted_enum", options={"values": [{"value": "SUCCESS", "weight": 92}, {"value": "FAILED", "weight": 5}, {"value": "PROCESSING", "weight": 3}]})],
    }
    return templates[template]


def default_rules() -> list[dict]:
    """Maintain the original simple command contract when no template is requested."""
    return [
        rule("name", "name_cn"),
        rule("gender", "weighted_enum", options={"values": ["男", "女"]}),
        rule("birth_date", "date_random", options={"start": "1970-01-01", "end": "2007-12-31"}),
        rule("phone", "mobile_cn"),
    ]


def _type_info(data_type: str) -> tuple[str, int | None, int | None, int | None]:
    """Return normalized SQL type, length, precision and scale."""
    normalized = re.sub(r"\s+", " ", str(data_type or "VARCHAR").strip()).upper()
    base_match = re.match(r"([A-Z][A-Z0-9_]*)", normalized)
    base = base_match.group(1) if base_match else "VARCHAR"
    numbers = [int(item) for item in re.findall(r"\d+", normalized)]
    if base in {"DECIMAL", "NUMERIC", "NUMBER"}:
        return base, numbers[0] if numbers else None, numbers[0] if numbers else None, numbers[1] if len(numbers) > 1 else 2
    return base, numbers[0] if numbers else None, None, None


def _field_rule(name: str, data_type: str = "VARCHAR", comment: str = "", **metadata) -> dict:
    base, length, precision, scale = _type_info(data_type)
    probe = f"{name} {comment}".lower()
    options: dict[str, object] = {}
    generator = "string_random"
    unique = bool(metadata.get("unique", False))

    if any(item in probe for item in ("mobile", "phone", "手机号", "电话")):
        generator = "mobile_cn"
    elif any(item in probe for item in ("email", "邮箱", "邮件")):
        generator = "email"
    elif any(item in probe for item in ("address", "地址")):
        generator = "china_address"
    elif any(item in probe for item in ("name", "姓名")):
        generator = "name_cn"
    elif any(item in probe for item in ("customer", "客户")) and "id" in probe:
        generator, unique, options = "sequence", True, {"prefix": "TEST-CUST-", "width": 8}
    elif any(item in probe for item in ("account", "账户", "账号")) and "id" in probe:
        generator, unique, options = "sequence", True, {"prefix": "TEST-ACCT-", "width": 12}
    elif any(item in probe for item in ("transaction", "trade", "流水", "交易")) and "id" in probe:
        generator, unique = "transaction_id", True
    elif any(item in probe for item in ("risk", "风险")):
        generator, options = "weighted_enum", {"values": ["低", "中", "高"]}
    elif any(item in probe for item in ("type", "类型", "status", "状态")):
        generator, options = "weighted_enum", {"values": ["默认值"]}
    elif base in {"BOOL", "BOOLEAN", "BIT"}:
        generator = "boolean_random"
    elif base in {"TINYINT", "SMALLINT", "INT", "INTEGER", "BIGINT", "SERIAL", "BIGSERIAL"}:
        generator = "integer_random"
    elif base in {"DECIMAL", "NUMERIC", "NUMBER", "REAL", "FLOAT", "DOUBLE", "MONEY"} or any(item in probe for item in ("amount", "balance", "金额", "余额")):
        generator = "decimal_random"
    elif base in {"DATE"}:
        generator = "date_random"
    elif base in {"DATETIME", "TIMESTAMP", "TIMESTAMPTZ", "TIME"}:
        generator = "datetime_random"
    elif base in {"UUID"} or name.lower() == "uuid":
        generator = "uuid"
    elif name.lower() == "id" or name.lower().endswith("_id"):
        generator, unique, options = "sequence", True, {"prefix": "TEST-", "width": 8}

    if length is not None and generator in {"string_random", "email", "name_cn", "template"}:
        options["length"] = length
    if precision is not None and generator == "decimal_random":
        options["scale"] = scale if scale is not None else 2
        options["max"] = float(10 ** max(1, min(12, precision - (scale or 0))) - 1)
    if metadata.get("auto_increment"):
        generator, unique, options = "sequence", True, {"start": 1, "width": max(1, length or 0)}
    if metadata.get("primary_key") or metadata.get("unique"):
        unique = True

    item = rule(name, generator, unique=unique, options=options)
    item["type"] = data_type
    if comment:
        item["comment"] = comment
    if metadata.get("primary_key"):
        item["primary_key"] = True
    if metadata.get("auto_increment"):
        item["auto_increment"] = True
    return item


def infer_rule(name: str, data_type: str, comment: str = "", **metadata) -> dict:
    return _field_rule(name, data_type, comment, **metadata)


def _unquote_sql_text(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        return value[1:-1].replace(value[0] * 2, value[0])
    return value


def _parse_type_and_tail(rest: str) -> tuple[str, str]:
    """Read a SQL type without mistaking nested type punctuation for constraints."""
    text = rest.strip()
    match = re.match(r"(?P<name>(?:DOUBLE\s+PRECISION|CHARACTER\s+VARYING|NATIONAL\s+CHARACTER(?:\s+VARYING)?|TIMESTAMP(?:\s+WITH(?:OUT)?\s+TIME\s+ZONE)?|TIME(?:\s+WITH(?:OUT)?\s+TIME\s+ZONE)?|[A-Za-z][A-Za-z0-9_]*))", text, re.I)
    if not match:
        raise PluginError("INPUT_INVALID", f"无法识别字段类型: {rest.strip()}")
    type_name = re.sub(r"\s+", " ", match.group("name")).upper()
    index = match.end()
    while index < len(text) and text[index].isspace():
        index += 1
    if text[index:index + 1] == "(":
        depth, quote, close = 1, None, index + 1
        while close < len(text):
            character = text[close]
            if quote:
                if character == quote:
                    if close + 1 < len(text) and text[close + 1] == quote:
                        close += 1
                    else:
                        quote = None
            elif character in {"'", '"'}:
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    close += 1
                    break
            close += 1
        if depth != 0:
            raise PluginError("INPUT_INVALID", f"字段类型括号不完整: {rest.strip()}")
        type_name += text[index:close].strip()
        index = close
    while index < len(text) and text[index].isspace():
        index += 1
    if text[index:index + 1] == "<":
        depth, quote, close = 1, None, index + 1
        while close < len(text):
            character = text[close]
            if quote:
                if character == quote:
                    quote = None
            elif character in {"'", '"'}:
                quote = character
            elif character == "<":
                depth += 1
            elif character == ">":
                depth -= 1
                if depth == 0:
                    close += 1
                    break
            close += 1
        if depth != 0:
            raise PluginError("INPUT_INVALID", f"字段类型尖括号不完整: {rest.strip()}")
        type_name += re.sub(r"\s+", " ", text[index:close]).upper()
        index = close
    if text[index:index + 2] == "[]":
        type_name += "[]"
        index += 2
    if re.match(r"\s+UNSIGNED\b", text[index:], re.I):
        unsigned = re.match(r"\s+UNSIGNED\b", text[index:], re.I)
        type_name += " UNSIGNED"
        index += unsigned.end()
    return type_name, text[index:].strip()


def _constraint_fields(rest: str) -> list[str]:
    match = re.search(r"\((?P<fields>.*)\)", rest, re.S)
    return _identifier_list(match.group("fields")) if match else []


def _parse_sql_table(table_name: str, body: str) -> list[dict]:
    definitions = split_fields(body)
    field_definitions: list[tuple[str, str, str]] = []
    constraints: list[dict] = []
    for definition in definitions:
        constraint = TABLE_CONSTRAINT_RE.match(definition)
        if constraint:
            kind = re.sub(r"\s+", " ", constraint.group("kind").upper())
            rest = constraint.group("rest").strip()
            fields = _constraint_fields(rest)
            reference = re.search(r"\bREFERENCES\s+(?P<table>.+?)\s*\((?P<fields>[^)]*)\)", rest, re.I | re.S)
            constraints.append({
                "name": _unquote_identifier(constraint.group("name") or ""),
                "type": kind,
                "fields": fields,
                "foreign_table": _normalize_table_name(reference.group("table")) if reference else "",
                "foreign_fields": _identifier_list(reference.group("fields")) if reference else [],
            })
            continue
        match = FIELD_RE.match(definition)
        if match:
            type_name, tail = _parse_type_and_tail(match.group("rest"))
            field_definitions.append((_normalize_table_name(match.group("field")), type_name, tail))
    if not field_definitions:
        raise PluginError("INPUT_INVALID", f"DDL 表 {table_name} 中未解析到字段")

    by_name = {name: index for index, (name, _, _) in enumerate(field_definitions)}
    primary_fields = {field for item in constraints if item["type"] == "PRIMARY KEY" for field in item["fields"]}
    unique_fields = {field for item in constraints if item["type"].startswith("UNIQUE") for field in item["fields"]}
    foreign_by_field: dict[str, tuple[str, str]] = {}
    for item in constraints:
        if item["type"] != "FOREIGN KEY":
            continue
        for index, field in enumerate(item["fields"]):
            references = item["foreign_fields"]
            foreign_by_field[field] = (item["foreign_table"], references[index] if index < len(references) else "")

    rules = []
    for name, data_type, tail in field_definitions:
        comment_match = re.search(r"\bCOMMENT\s+(['\"])((?:\\.|(?!\1).)*)\1", tail, re.I | re.S)
        primary = bool(re.search(r"\bPRIMARY\s+KEY\b", tail, re.I)) or name in primary_fields
        unique = bool(re.search(r"\bUNIQUE(?:\s+KEY|\s+INDEX)?\b", tail, re.I)) or name in unique_fields
        auto_increment = bool(re.search(r"\b(AUTO_INCREMENT|AUTOINCREMENT|IDENTITY(?:\s*\(|\b)|SERIAL|BIGSERIAL|SMALLSERIAL|GENERATED\s+(?:ALWAYS|BY\s+DEFAULT)\s+AS\s+IDENTITY)\b", f"{data_type} {tail}", re.I))
        nullable = not bool(re.search(r"\bNOT\s+NULL\b", tail, re.I))
        default_match = re.search(r"\bDEFAULT\s+(?P<value>.+?)(?=\s+(?:NOT\s+NULL|NULL|PRIMARY\s+KEY|UNIQUE|REFERENCES|COMMENT|CHECK|CONSTRAINT|COLLATE|ON\s+UPDATE)\b|$)", tail, re.I | re.S)
        foreign_table, foreign_field = foreign_by_field.get(name, ("", ""))
        item = infer_rule(
            name,
            data_type,
            _unquote_sql_text(comment_match.group(2)) if comment_match else "",
            primary_key=primary,
            unique=unique,
            auto_increment=auto_increment,
        )
        item["nullable"] = nullable
        if default_match:
            default = default_match.group("value").strip()
            if default.upper() not in {"NULL", "CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME"}:
                item["default"] = _unquote_sql_text(default)
        if foreign_table:
            item["foreign_table"] = foreign_table
            item["foreign_field"] = foreign_field
        rules.append(item)
    return rules


def sql_tables(path: Path) -> list[dict]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PluginError("INPUT_INVALID", "无法读取 SQL 文件") from error
    tables = [{"table": name, "fields": _parse_sql_table(name, body)} for name, body in all_create_table_bodies(content)]
    if not tables:
        raise PluginError("INPUT_INVALID", "未找到支持的 CREATE TABLE 语句")
    return tables


def sql_rules(path: Path) -> list[dict]:
    """Backward-compatible single-table view of SQL rules."""
    return sql_tables(path)[0]["fields"]


def _read_excel_rows(path: Path) -> list[list[str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = ["".join(item.itertext()) for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))]
            sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise PluginError("INPUT_INVALID", "Excel 字段清单格式不支持") from error
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rows = []
    for row in sheet.findall(f".//{namespace}row"):
        values = []
        for cell in row.findall(f"{namespace}c"):
            value = cell.find(f"{namespace}v")
            inline = cell.find(f".//{namespace}t")
            values.append(inline.text if inline is not None else shared[int(value.text)] if value is not None and cell.get("t") == "s" else value.text if value is not None else "")
        rows.append(values)
    return rows


def excel_tables(path: Path) -> list[dict]:
    rows = _read_excel_rows(path)
    if len(rows) < 2:
        raise PluginError("INPUT_INVALID", "Excel 字段清单至少需要表头和一行字段")
    headers = [item.strip().lower() for item in rows[0]]
    aliases = {
        "table": {"table", "table_name", "表", "表名"},
        "field": {"field", "name", "字段", "字段名"},
        "type": {"type", "类型", "字段类型", "数据类型"},
        "comment": {"comment", "注释", "说明", "备注"},
        "generator": {"generator", "生成器", "规则"},
        "options": {"options", "选项", "参数"},
        "unique": {"unique", "唯一"},
        "nullable_rate": {"nullable_rate", "可空比例", "空值比例"},
        "primary_key": {"primary_key", "主键"},
        "nullable": {"nullable", "可空"},
        "auto_increment": {"auto_increment", "自增"},
    }
    positions = {key: next((index for index, header in enumerate(headers) if header in names), None) for key, names in aliases.items()}
    if positions["field"] is None:
        raise PluginError("INPUT_INVALID", "Excel 字段清单缺少字段名列")
    grouped: dict[str, list[dict]] = {}
    default_table = path.stem or "导入表"
    for row in rows[1:]:
        get = lambda key, default="": row[positions[key]] if positions[key] is not None and positions[key] < len(row) else default
        name = get("field").strip()
        if not name:
            continue
        table_name = get("table").strip() or default_table
        item = infer_rule(name, get("type", "VARCHAR") or "VARCHAR", get("comment").strip(), unique=str(get("unique")).lower() in {"true", "1", "yes", "是"}, primary_key=str(get("primary_key")).lower() in {"true", "1", "yes", "是"}, auto_increment=str(get("auto_increment")).lower() in {"true", "1", "yes", "是"})
        if get("generator").strip():
            item["generator"] = get("generator").strip()
        if get("options").strip():
            try:
                item["options"] = json.loads(get("options"))
            except json.JSONDecodeError as error:
                raise PluginError("INPUT_INVALID", f"字段 {name} 的 options 必须是 JSON 对象") from error
        if get("nullable_rate").strip():
            try:
                item["nullable_rate"] = float(get("nullable_rate"))
            except ValueError as error:
                raise PluginError("INPUT_INVALID", f"字段 {name} 的 nullable_rate 无效") from error
        if get("nullable").strip():
            item["nullable"] = str(get("nullable")).lower() not in {"false", "0", "no", "否"}
        grouped.setdefault(table_name, []).append(item)
    tables = [{"table": table, "fields": fields} for table, fields in grouped.items() if fields]
    if not tables:
        raise PluginError("INPUT_INVALID", "Excel 字段清单未解析到字段")
    return tables


def excel_rules(path: Path) -> list[dict]:
    return excel_tables(path)[0]["fields"]

def delimited_text(rows: list[dict[str, object]], delimiter: str, include_header: bool) -> str:
    if len(delimiter) != 1:
        raise PluginError("INVALID_PARAMS", "txt_delimiter 必须为单个字符")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), delimiter=delimiter, lineterminator="\n")
    if include_header:
        writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def sql_identifier(value: str, dialect: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*", value):
        raise PluginError("INVALID_PARAMS", "sql_table 只能包含字母、数字、下划线和点")
    left, right = {"mysql": ("`", "`"), "postgresql": ('"', '"'), "sqlite": ('"', '"'), "sqlserver": ("[", "]"), "oracle": ('"', '"')}[dialect]
    return ".".join(f"{left}{part}{right}" for part in value.split("."))


def sql_literal(value: object, dialect: str) -> str:
    if value is None: return "NULL"
    if isinstance(value, bool): return ("1" if value else "0") if dialect in {"mysql", "sqlserver", "oracle"} else ("TRUE" if value else "FALSE")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))): raise PluginError("INVALID_PARAMS", "SQL 不支持 NaN 或无穷数值")
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _sql_type(item: dict) -> str:
    data_type = re.sub(r"\s+", " ", str(item.get("type") or "VARCHAR").strip()).upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*(?:\s*\([^()]{1,32}\))?", data_type):
        raise PluginError("INVALID_PARAMS", f"字段 {item.get('name')} 的数据库类型无效")
    return data_type


def sql_script(rows: list[dict[str, object]], params: dict, rules: list[dict] | None = None) -> str:
    dialect = params.get("sql_dialect", "mysql")
    table = sql_identifier(params.get("sql_table", "test_data"), dialect)
    columns = [sql_identifier(str(column), dialect) for column in rows[0]]
    rule_map = {item.get("name"): item for item in (rules or [])}
    ddl = ""
    if params.get("sql_create_table"):
        definitions = []
        for column in rows[0]:
            item = rule_map.get(column, {"name": column})
            definition = f"{sql_identifier(str(column), dialect)} {_sql_type(item)}"
            if item.get("primary_key"):
                definition += " PRIMARY KEY"
            elif item.get("unique"):
                definition += " UNIQUE"
            if item.get("nullable", True) is False or item.get("required") or item.get("primary_key"):
                definition += " NOT NULL"
            if "default" in item and item.get("default") is not None:
                definition += f" DEFAULT {sql_literal(item.get('default'), dialect)}"
            if item.get("auto_increment"):
                definition += {"mysql": " AUTO_INCREMENT", "postgresql": " GENERATED BY DEFAULT AS IDENTITY", "sqlserver": " IDENTITY(1,1)", "oracle": " GENERATED BY DEFAULT AS IDENTITY", "sqlite": " AUTOINCREMENT"}[dialect]
            definitions.append(definition)
        ddl = f"CREATE TABLE {table} (\n  " + ",\n  ".join(definitions) + "\n);\n\n"
    batch_size = params.get("sql_batch_size", 500)
    values = ["(" + ", ".join(sql_literal(row.get(column), dialect) for column in rows[0]) + ")" for row in rows]
    statements = []
    if dialect == "oracle":
        for offset in range(0, len(values), batch_size):
            batch = values[offset:offset + batch_size]
            statements.append("INSERT ALL\n" + "\n".join(f"  INTO {table} (" + ", ".join(columns) + f") VALUES {value}" for value in batch) + "\nSELECT 1 FROM DUAL;")
    else:
        for offset in range(0, len(values), batch_size):
            batch = values[offset:offset + batch_size]
            statements.append(f"INSERT INTO {table} (" + ", ".join(columns) + ") VALUES\n  " + ",\n  ".join(batch) + ";")
    if params.get("sql_transaction", True):
        if dialect == "sqlserver": return ddl + "BEGIN TRANSACTION;\n\n" + "\n\n".join(statements) + "\n\nCOMMIT TRANSACTION;\n"
        return ddl + "BEGIN;\n\n" + "\n\n".join(statements) + "\n\nCOMMIT;\n"
    return ddl + "\n\n".join(statements) + "\n"


def render_output(rows: list[dict[str, object]], output_format: str, params: dict, task_id: str, rules: list[dict] | None = None) -> tuple[str, bytes]:
    name = f"{task_id}.{output_format}"
    if output_format == "json": return name, json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
    if output_format == "csv": return name, delimited_text(rows, ",", True).encode("utf-8")
    if output_format == "xlsx": return name, xlsx(rows)
    if output_format == "txt": return name, delimited_text(rows, params.get("txt_delimiter", "|"), params.get("txt_header", True)).encode("utf-8")
    if output_format == "sql": return name, sql_script(rows, params, rules).encode("utf-8")
    raise PluginError("INVALID_PARAMS", f"不支持的输出格式: {output_format}")


def zip_output(rows: list[dict[str, object]], params: dict, task_id: str, rules: list[dict] | None = None) -> bytes:
    formats = params.get("zip_formats", ["json", "csv", "xlsx", "txt", "sql"])
    if not formats or any(item not in {"json", "csv", "xlsx", "txt", "sql"} for item in formats):
        raise PluginError("INVALID_PARAMS", "zip_formats 必须是 json、csv、xlsx、txt、sql 的非空列表")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for output_format in formats:
            name, content = render_output(rows, output_format, params, task_id, rules)
            archive.writestr(name, content)
        archive.writestr("generation-summary.json", json.dumps({"count": len(rows), "formats": formats, "sql_dialect": params.get("sql_dialect", "mysql")}, ensure_ascii=False, indent=2))
    return output.getvalue()


class Plugin:
    def init(self, context):
        self.context = context
        data_dir = Path(__file__).resolve().parents[1] / "data"
        self.data_metadata = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
        metadata = self.data_metadata["administrative_divisions"]
        self.mainland_regions = self._load_resource(data_dir, metadata["mainland_file"], metadata["mainland_sha256"])
        self.special_regions = self._load_resource(data_dir, metadata["hk_mo_tw_file"], metadata["hk_mo_tw_sha256"])

    @staticmethod
    def _load_resource(data_dir: Path, file_name: str, expected_sha256: str) -> dict:
        content = (data_dir / file_name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise PluginError("EXECUTION_FAILED", f"内置行政区划资源校验失败: {file_name}")
        return json.loads(content)

    def _normalize_rule(self, item: dict) -> dict:
        if not isinstance(item, dict):
            raise PluginError("INVALID_PARAMS", "字段定义必须是对象")
        name = str(item.get("name", item.get("field", ""))).strip()
        if not name:
            raise PluginError("INVALID_PARAMS", "字段定义必须包含 name")
        result = dict(item)
        result["name"] = name
        result["options"] = dict(item.get("options") or {})
        data_type = item.get("type") or item.get("data_type") or "VARCHAR"
        result["type"] = str(data_type)
        if not item.get("generator"):
            result["generator"] = infer_rule(name, result["type"], str(item.get("comment", ""))).get("generator")
        if result["generator"] == "phone_cn":
            result["generator"] = "mobile_cn"
        # A field type is also a useful contract when the user does not want to
        # understand generator names. Keep explicit generator/options authoritative.
        base, length, precision, scale = _type_info(result["type"])
        options = result["options"]
        if length is not None and "length" not in options and result["generator"] in {"string_random", "email", "name_cn", "template"}:
            options["length"] = length
        if result["generator"] == "decimal_random" and scale is not None and "scale" not in options:
            options["scale"] = scale
        if result["generator"] == "integer_random" and "min" not in options and "max" not in options:
            limits = {"TINYINT": (-128, 127), "SMALLINT": (-32768, 32767), "INT": (-2147483648, 2147483647), "INTEGER": (-2147483648, 2147483647), "BIGINT": (-9223372036854775808, 9223372036854775807)}
            if base in limits:
                options.update({"min": limits[base][0], "max": limits[base][1]})
        result["options"] = options
        return result

    def _source_schema(self, params: dict) -> list[dict]:
        source = Path(params["source_file"])
        source_format = params.get("source_format")
        if source_format == "sql":
            return sql_tables(source)
        if source_format == "excel":
            return excel_tables(source)
        raise PluginError("INVALID_PARAMS", "source_file 需要 source_format=sql 或 excel")

    def _rules(self, params: dict) -> list[dict]:
        self._source_table = params.get("table") or params.get("sql_table")
        modes = [key for key in ("fields", "rules", "rule_set", "source_file", "template") if key in params and params.get(key) is not None]
        if len(modes) > 1:
            raise PluginError("INVALID_PARAMS", "自定义字段、规则集、表结构导入和快捷模板只能选择一种模式")
        if "fields" in modes:
            raw = params["fields"]
            if not isinstance(raw, list) or not raw:
                raise PluginError("INVALID_PARAMS", "请至少添加一个字段")
            return [self._normalize_rule(item) for item in raw]
        if "rules" in modes:
            raw = params["rules"]
            if not isinstance(raw, list) or not raw:
                raise PluginError("INVALID_PARAMS", "rules 必须是非空字段数组")
            return [self._normalize_rule(item) for item in raw]
        if params.get("rule_set"):
            try:
                loaded = json.loads(Path(params["rule_set"]).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise PluginError("INPUT_INVALID", "无法读取规则集 JSON") from error
            if isinstance(loaded, dict):
                self._source_table = loaded.get("table") or loaded.get("table_name") or self._source_table
                raw = loaded.get("fields") or loaded.get("rules")
            else:
                raw = loaded
            if not isinstance(raw, list):
                raise PluginError("INPUT_INVALID", "规则集必须是字段数组，或包含 fields 的对象")
            return [self._normalize_rule(item) for item in raw]
        if params.get("source_file"):
            tables = self._source_schema(params)
            requested = str(params.get("source_table") or "").strip()
            if requested:
                selected = next((item for item in tables if item["table"] == requested), None)
                if selected is None:
                    available = ", ".join(item["table"] for item in tables)
                    raise PluginError("INPUT_INVALID", f"未找到导入表 {requested}，可选表：{available}")
            elif len(tables) == 1:
                selected = tables[0]
            else:
                names = ", ".join(item["table"] for item in tables)
                raise PluginError("INVALID_PARAMS", f"导入文件包含多张表，请选择 source_table（可选：{names}）")
            if not self._source_table:
                self._source_table = selected["table"]
            return [self._normalize_rule(item) for item in selected["fields"]]
        if params.get("template"):
            return [self._normalize_rule(item) for item in template_rules(params["template"])]
        raise PluginError("INVALID_PARAMS", "请至少添加一个字段，或选择表结构导入、规则集或快捷模板")

    def _address(self, options: dict, rng: random.Random) -> str:
        provinces = set(options.get("province", []))
        cities = set(options.get("city", []))
        districts = set(options.get("district", []))
        combined = {**self.mainland_regions, **self.special_regions}
        province_options = [(name, value) for name, value in combined.items() if not provinces or name in provinces]
        if not province_options: raise PluginError("INVALID_PARAMS", "地址省份筛选无匹配项")
        province, city_map = rng.choice(province_options)
        city_options = [(name, value) for name, value in city_map.items() if not cities or name in cities]
        if not city_options: raise PluginError("INVALID_PARAMS", "地址城市筛选与省份不匹配")
        city, district_map = rng.choice(city_options)
        if isinstance(district_map, list):
            district_options = [(name, []) for name in district_map if not districts or name in districts]
        else:
            district_options = [(name, value) for name, value in district_map.items() if not districts or name in districts]
        if not district_options: raise PluginError("INVALID_PARAMS", "地址区县筛选与省市不匹配")
        district, streets = rng.choice(district_options)
        granularity = options.get("granularity", "full")
        if granularity == "province": return province
        if granularity == "city": return province + city
        address = province + city + district
        if granularity == "district": return address
        street_mode = options.get("street_mode", "virtual" if options.get("include_virtual_street", True) else "none")
        if street_mode == "real" and streets: return address + rng.choice(streets)
        if street_mode == "none": return address
        return address + f"测试路{rng.randint(1, 999)}号"

    def _unique_capacity(self, item: dict) -> int | None:
        """Return a finite generator capacity when it can be calculated safely."""
        generator, options = item.get("generator"), item.get("options", {})
        if generator == "mobile_cn":
            configured = options.get("prefixes") or self.context.config.get("phone_prefixes") or MOBILE_PREFIXES
            prefixes = configured.split(",") if isinstance(configured, str) else configured
            return len(set(prefixes)) * 100_000_000
        if generator == "name_cn":
            lengths = options.get("given_name_length", [1, 2])
            return len(SURNAMES) * sum(len(GIVEN) ** int(length) for length in lengths)
        if generator in {"weighted_enum", "constant"}:
            values = options.get("values", []) if generator == "weighted_enum" else [options.get("value")]
            return len({entry.get("value") if isinstance(entry, dict) else entry for entry in values})
        if generator == "string_random" and options.get("length"):
            alphabet = str(options.get("alphabet", "abcdefghijklmnopqrstuvwxyz0123456789"))
            return len(alphabet) ** int(options["length"])
        if generator == "template" and "{index}" not in str(options.get("value", "")):
            return 1
        return None

    def _value(self, item: dict, index: int, rng: random.Random) -> object:
        generator, options = item.get("generator"), item.get("options", {})
        if generator == "sequence":
            return f"{options.get('prefix', '')}{int(options.get('start', 1)) + index:0{int(options.get('width', 0))}d}"
        if generator == "name_cn":
            return rng.choice(SURNAMES) + "".join(rng.choice(GIVEN) for _ in range(rng.choice(options.get("given_name_length", [1, 2]))))
        if generator == "mobile_cn":
            configured = options.get("prefixes") or self.context.config.get("phone_prefixes") or MOBILE_PREFIXES
            prefixes = tuple(item.strip() for item in configured.split(",")) if isinstance(configured, str) else tuple(map(str, configured))
            if not prefixes or any(not re.fullmatch(r"\d{3}", prefix) for prefix in prefixes):
                raise PluginError("INVALID_PARAMS", f"字段 {item.get('name')} 的手机号前缀必须是 3 位数字")
            return rng.choice(prefixes) + f"{rng.randrange(100000000):08d}"
        if generator == "email":
            length = int(options.get("length", 0) or 0)
            value = f"test{index + 1}@example.test"
            if length and len(value) > length:
                local_length = max(1, length - len("@example.test"))
                value = ("u" * local_length)[:local_length] + "@example.test"
                if len(value) > length:
                    value = value[:length]
            return value
        if generator == "china_address":
            return self._address(options, rng)
        if generator in {"date_random", "datetime_random"}:
            start_text = options.get("start", "2020-01-01T00:00:00")
            end_text = options.get("end", "2026-12-31T23:59:59")
            start = datetime.fromisoformat(str(start_text))
            end = datetime.fromisoformat(str(end_text))
            if end < start:
                raise PluginError("INVALID_PARAMS", f"字段 {item.get('name')} 的时间范围无效")
            value = start + timedelta(seconds=rng.randrange(int((end - start).total_seconds()) + 1))
            return value.date().isoformat() if generator == "date_random" else value.isoformat(sep=" ")
        if generator == "decimal_random":
            low, high = float(options.get("min", 0)), float(options.get("max", 1))
            if high < low:
                raise PluginError("INVALID_PARAMS", f"字段 {item.get('name')} 的数值范围无效")
            return round(rng.uniform(low, high), int(options.get("scale", 2)))
        if generator == "integer_random":
            low, high = int(options.get("min", 0)), int(options.get("max", 1000000))
            if high < low:
                raise PluginError("INVALID_PARAMS", f"字段 {item.get('name')} 的整数范围无效")
            return rng.randint(low, high)
        if generator == "boolean_random":
            return bool(rng.randrange(2))
        if generator == "string_random":
            length = options.get("length")
            if length is None:
                length = rng.randint(int(options.get("min_length", 8)), int(options.get("max_length", 16)))
            length = int(length)
            if length < 0:
                raise PluginError("INVALID_PARAMS", f"字段 {item.get('name')} 的 length 不能小于 0")
            alphabet = str(options.get("alphabet", "abcdefghijklmnopqrstuvwxyz0123456789"))
            if not alphabet:
                raise PluginError("INVALID_PARAMS", f"字段 {item.get('name')} 的 alphabet 不能为空")
            return "".join(rng.choice(alphabet) for _ in range(length))
        if generator == "constant":
            return options.get("value")
        if generator == "weighted_enum":
            values = options.get("values", [])
            if not values:
                raise PluginError("INVALID_PARAMS", f"{item.get('name')} 的枚举值不能为空")
            pairs = [(entry.get("value"), entry.get("weight", 1)) if isinstance(entry, dict) else (entry, 1) for entry in values]
            try:
                return rng.choices([pair[0] for pair in pairs], weights=[float(pair[1]) for pair in pairs], k=1)[0]
            except (TypeError, ValueError) as error:
                raise PluginError("INVALID_PARAMS", f"{item.get('name')} 的枚举权重无效") from error
        if generator == "transaction_id":
            return f"TEST-TXN-{index + 1:08d}"
        if generator == "uuid":
            return f"TEST-{uuid.UUID(int=rng.getrandbits(128))}"
        if generator == "template":
            return str(options.get("value", "TEST-{index}")).format(index=index + 1)
        raise PluginError("INVALID_PARAMS", f"不支持的生成器: {generator}")

    def execute(self, command, params):
        if command != "data.mock":
            raise PluginError("INVALID_PARAMS", "不支持的命令")
        if params.get("preview"):
            if not params.get("source_file"):
                raise PluginError("INVALID_PARAMS", "预览表结构必须提供 source_file")
            tables = self._source_schema(params)
            return Result("success", f"已解析 {len(tables)} 张表", {"tables": [{"name": item["table"], "fields": item["fields"]} for item in tables]}, [])
        rules = self._rules(params)
        rules = [item for item in rules if item.get("enabled", True)]
        if not rules:
            raise PluginError("INVALID_PARAMS", "至少需要一个启用字段")
        names = [item["name"] for item in rules]
        if len(names) != len(set(names)):
            raise PluginError("INVALID_PARAMS", "字段名不能重复")
        for item in rules:
            if item.get("unique"):
                capacity = self._unique_capacity(item)
                if capacity is not None and params["count"] > capacity:
                    raise PluginError("INVALID_PARAMS", f"字段 {item['name']} 的唯一值容量为 {capacity}，不足以生成 {params['count']} 条")
        rng, unique_values, rows = random.Random(params.get("seed")), {}, []
        for index in range(params["count"]):
            row = {}
            for item in rules:
                name, attempts = item["name"], 0
                try:
                    nullable_rate = float(item.get("nullable_rate", 0))
                except (TypeError, ValueError) as error:
                    raise PluginError("INVALID_PARAMS", f"字段 {name} 的 nullable_rate 无效") from error
                if not 0 <= nullable_rate <= 1:
                    raise PluginError("INVALID_PARAMS", f"字段 {name} 的 nullable_rate 必须在 0 到 1 之间")
                if rng.random() < nullable_rate:
                    row[name] = None
                    continue
                while True:
                    value = self._value(item, index, rng)
                    if not item.get("unique") or value not in unique_values.setdefault(name, set()):
                        break
                    attempts += 1
                    if attempts >= 1000:
                        raise PluginError("INVALID_PARAMS", f"字段 {name} 的唯一值容量不足")
                if item.get("unique"):
                    unique_values[name].add(value)
                row[name] = value
            rows.append(row)
        output_format = params["format"]
        output_params = dict(params)
        if not output_params.get("sql_table") and self._source_table:
            output_params["sql_table"] = self._source_table
        if output_format == "zip":
            name = f"{self.context.task.id}.zip"
            self.context.files.write_bytes(name, zip_output(rows, output_params, self.context.task.id, rules))
            output_details = {"zip_formats": output_params.get("zip_formats", ["json", "csv", "xlsx", "txt", "sql"])}
        else:
            name, content = render_output(rows, output_format, output_params, self.context.task.id, rules)
            self.context.files.write_bytes(name, content)
            output_details = {"format": output_format}
        self.context.logger.info(f"生成 {len(rows)} 条测试数据，启用 {len(rules)} 个字段规则")
        return Result("success", f"已生成 {len(rows)} 条模拟测试数据", {"count": len(rows), "seed": params.get("seed"), "table": output_params.get("sql_table") or self._source_table, "source_table": params.get("source_table") or self._source_table, "fields": [item["name"] for item in rules], "unique_fields": sorted(unique_values), "administrative_divisions_version": self.data_metadata["administrative_divisions"]["version"], **output_details}, [name])

    def destroy(self):
        pass
