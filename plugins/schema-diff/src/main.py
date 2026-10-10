"""Local, conservative structural SQL inspection. No database or plugin imports.

The tokenizer is shared by all grammars: separators are recognized only outside
quoted tokens, comments and parentheses. Unsupported grammar remains evidence.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import json
import re
from typing import Any

from testbox.sdk import PluginError, Result, read_dataset, run_dataset_batch

ATTRS = ("type", "length", "precision", "scale", "nullable", "default",
         "primary_key", "unique", "auto_increment", "comment", "foreign_table", "foreign_field")
BOOLS = {"nullable", "primary_key", "unique", "auto_increment"}
DIMENSIONS = {"length", "precision", "scale"}
MAX_TEXT = 20 * 1024 * 1024
MAX_TOKENS = 200_000
MAX_STATEMENTS = 10_000
MAX_FIELDS = 100_000
DISPLAY_LIMIT = 100
QUERY_UNKNOWN = ("type", "length", "precision", "scale", "nullable", "default",
                 "primary_key", "unique", "auto_increment", "comment", "foreign_table", "foreign_field")


@dataclass(frozen=True)
class Token:
    text: str
    kind: str
    start: int
    end: int

    @property
    def upper(self):
        return self.text.upper() if self.kind == "word" else ""


def fail(message: str, location: dict | None = None):
    raise PluginError("SQL_PARSE_ERROR", message, details={"location": location or {}})


def tokenize(text: str, base: dict) -> list[Token]:
    if len(text.encode("utf-8")) > MAX_TEXT:
        raise PluginError("INPUT_LIMIT_EXCEEDED", "SQL 文本超过 20 MiB")
    tokens: list[Token] = []
    i, depth = 0, 0
    while i < len(text):
        start, c = i, text[i]
        if c.isspace():
            i += 1
            continue
        if text.startswith("--", i):
            end = text.find("\n", i + 2)
            i = len(text) if end < 0 else end + 1
            continue
        if text.startswith("/*", i):
            nesting = 1
            i += 2
            while i < len(text) and nesting:
                if text.startswith("/*", i):
                    nesting += 1
                    i += 2
                elif text.startswith("*/", i):
                    nesting -= 1
                    i += 2
                else:
                    i += 1
            if nesting:
                fail("未闭合块注释", base)
            # Executable dialect comments must never be silently dropped.
            if text[start:start + 3] in ("/*!", "/*+"):
                tokens.append(Token(text[start:i], "unsupported", start, i))
            continue
        if c in "'\"`[":
            closing = "]" if c == "[" else c
            i += 1
            ambiguous = False
            while i < len(text):
                if text[i] == closing:
                    if i + 1 < len(text) and text[i + 1] == closing:
                        i += 2
                        continue
                    i += 1
                    break
                if c == "'" and text[i] == "\\":
                    # Protect separators, but do not assume a dialect's escape mode.
                    ambiguous = True
                    i += 2
                else:
                    i += 1
            else:
                fail("未闭合 SQL 引号", base)
            tokens.append(Token(text[start:i], "unsupported" if ambiguous else ("string" if c == "'" else "identifier"), start, i))
        elif c == "$" and (i + 1 < len(text) and (text[i + 1] == "$" or text[i + 1].isalpha() or text[i + 1] == "_")):
            end = i + 1
            while end < len(text) and (text[end].isalnum() or text[end] == "_"):
                end += 1
            if end < len(text) and text[end] == "$":
                tag = text[i:end + 1]
                close = text.find(tag, end + 1)
                if close < 0:
                    fail("未闭合 dollar quote", base)
                i = close + len(tag)
                tokens.append(Token(text[start:i], "unsupported", start, i))
            else:
                i += 1
                tokens.append(Token(c, "symbol", start, i))
        elif c.isalpha() or c == "_":
            i += 1
            while i < len(text) and (text[i].isalnum() or text[i] in "_$"):
                i += 1
            tokens.append(Token(text[start:i], "word", start, i))
        elif c.isdigit():
            match = re.match(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text[i:])
            i += len(match.group(0))
            tokens.append(Token(text[start:i], "number", start, i))
        else:
            i += 1
            if c == "(":
                depth += 1
                if depth > 128:
                    raise PluginError("INPUT_LIMIT_EXCEEDED", "SQL 括号深度超过 128")
            elif c == ")":
                depth -= 1
                if depth < 0:
                    fail("多余的右括号", base)
            elif c == ";" and depth:
                fail("括号内出现未引用分号", base)
            tokens.append(Token(c, "symbol", start, i))
        if len(tokens) > MAX_TOKENS:
            raise PluginError("INPUT_LIMIT_EXCEEDED", "SQL token 数超过限制")
    if depth:
        fail("未闭合 SQL 括号", base)
    return tokens


def identifier(token: Token) -> str:
    if token.kind == "identifier":
        closing = "]" if token.text[0] == "[" else token.text[0]
        return token.text[1:-1].replace(closing * 2, closing)
    if token.kind == "word":
        return token.text
    fail("应为标识符")


def qualified(tokens: list[Token], index: int = 0) -> tuple[str, int]:
    if index >= len(tokens):
        fail("缺少标识符")
    parts = [identifier(tokens[index])]
    index += 1
    while index < len(tokens) and tokens[index].text == ".":
        if index + 1 >= len(tokens):
            fail("限定标识符缺少名称")
        parts.append(identifier(tokens[index + 1]))
        index += 2
    return ".".join(parts), index



def qualified_parts(tokens: list[Token], index: int = 0) -> tuple[list[str], int]:
    _, end = qualified(tokens, index)
    return [identifier(tokens[i]) for i in range(index, end, 2)], end


def set_table(statement: dict, tokens: list[Token], start: int):
    parts, _ = qualified_parts(tokens, start)
    statement["table_parts"] = parts
    statement["schema"] = ".".join(parts[:-1])


def levels(tokens: list[Token]):
    depth = 0
    for i, token in enumerate(tokens):
        yield i, token, depth
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1


def split_top(tokens: list[Token], delimiter: str = ",") -> list[list[Token]]:
    parts, start = [], 0
    for i, token, depth in levels(tokens):
        if not depth and token.text == delimiter:
            parts.append(tokens[start:i])
            start = i + 1
    parts.append(tokens[start:])
    if any(not part for part in parts):
        fail("列表包含空项或多余分隔符")
    return parts


def closing_index(tokens: list[Token], index: int) -> int:
    if index >= len(tokens) or tokens[index].text != "(":
        fail("缺少左括号")
    depth = 0
    for i in range(index, len(tokens)):
        if tokens[i].text == "(":
            depth += 1
        elif tokens[i].text == ")":
            depth -= 1
            if depth == 0:
                return i
    fail("缺少右括号")


def canonical(tokens: list[Token], *, upper: bool = False) -> str:
    """Token spelling normalization, never a semantic SQL equivalence claim."""
    result = ""
    previous = ""
    for token in tokens:
        value = token.text.upper() if upper and token.kind == "word" else token.text
        if result and value not in (",", ")", ".") and previous not in ("(", ".") and value != "(":
            result += " "
        result += value
        previous = value
    return result


def issue(statement: dict, code: str, message: str, *, fields: list[str] | None = None, location: dict | None = None):
    statement["unknown"].append({"code": code, "message": message,
                                  "fields": fields or [], "location": location or statement["location"]})
    statement["complete"] = False


def new_statement(kind: str, table: str, location: dict) -> dict:
    return {"kind": kind, "table": table, "fields": [], "location": location,
            "complete": True, "field_set_complete": True, "unsupported": False, "unknown": [],
            "primary_key_fields": [], "primary_key_order_known": kind == "create"}


def unsupported(statement: dict, message: str, *, field_set: bool = False):
    statement["unsupported"] = True
    if field_set:
        statement["field_set_complete"] = False
    issue(statement, "UNSUPPORTED", message)
    return statement


def field_record(table: str, field: str, kind: str, location: dict) -> dict:
    return {"table": table, "schema": table.rsplit(".", 1)[0] if "." in table else "",
            "field": field, "kind": kind, "declared": kind in {"create", "structure"},
            "source_field": None, "source_fields": None, "expression": None, "alias": None,
            **dict.fromkeys(ATTRS), "known": [], "location": location}



def inspect_expression(tokens: list[Token], *, wildcard: bool = False) -> tuple[bool, list[str]]:
    """Validate a deliberately small expression grammar, collect references.

    Supports literals, qualified fields, function calls, parentheses, unary +/-
    and binary arithmetic/comparison/concatenation. CAST/CASE/window/dialect
    extensions are unsupported, not evaluated or guessed. Function names are not
    mistaken for field references. A function's * argument is not a projection *.
    """
    sources = []
    index = 0
    operators = {"+", "-", "*", "/", "%", "=", "<", ">", "!", "|", "&", "^"}
    constants = {"NULL", "TRUE", "FALSE", "CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_TIME"}

    def atom():
        nonlocal index
        if index >= len(tokens):
            fail("表达式缺少操作数")
        token = tokens[index]
        if token.text in {"+", "-"}:
            index += 1
            return atom()
        if token.text == "*" and wildcard and len(tokens) == 1:
            index += 1
            return True
        if token.text == "(":
            end = closing_index(tokens, index)
            supported, fields = inspect_expression(tokens[index + 1:end])
            sources.extend(fields)
            index = end + 1
            return supported
        if token.kind in {"string", "number"}:
            index += 1
            return True
        if token.kind in {"word", "identifier"}:
            if token.upper in {"CASE", "CAST", "SELECT", "EXISTS", "INTERVAL"}:
                return False
            name, end = qualified(tokens, index)
            index = end
            if index < len(tokens) and tokens[index].text == "(":
                close = closing_index(tokens, index)
                arguments = tokens[index + 1:close]
                supported = True
                if arguments:
                    for group in split_top(arguments):
                        valid, fields = inspect_expression(group, wildcard=True)
                        supported = supported and valid
                        sources.extend(fields)
                index = close + 1
                return supported
            if token.upper not in constants:
                sources.append(name)
            return True
        return False

    if not tokens or not atom():
        return False, list(dict.fromkeys(sources))
    while index < len(tokens):
        if tokens[index].text not in operators:
            return False, list(dict.fromkeys(sources))
        first = tokens[index].text
        index += 1
        # Recognize only conventional paired operators, not arbitrary runs.
        operator = first
        if index < len(tokens) and first + tokens[index].text in {"<=", ">=", "<>", "!=", "||"}:
            operator += tokens[index].text
            index += 1
        if operator == "!":
            return False, list(dict.fromkeys(sources))
        if not atom():
            return False, list(dict.fromkeys(sources))
    return True, list(dict.fromkeys(sources))


# Finite type grammar: unfamiliar types are retained but explicitly unsupported.
SIMPLE_TYPES = {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "MEDIUMINT",
                "BOOL", "BOOLEAN", "REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "NUMBER",
                "CHAR", "VARCHAR", "VARCHAR2", "NCHAR", "NVARCHAR", "NVARCHAR2", "TEXT", "NTEXT",
                "DATE", "TIME", "TIMESTAMP", "DATETIME", "DATETIME2", "TIMESTAMPTZ", "BLOB",
                "BINARY", "VARBINARY", "BYTEA", "JSON", "JSONB", "UUID", "UNIQUEIDENTIFIER"}
NUMERIC_TYPES = {"DECIMAL", "NUMERIC", "NUMBER"}
CHAR_TYPES = {"CHAR", "VARCHAR", "VARCHAR2", "NCHAR", "NVARCHAR", "NVARCHAR2", "BINARY", "VARBINARY"}


def consume_type(tokens: list[Token], index: int) -> tuple[str, dict, int, bool]:
    if index >= len(tokens) or tokens[index].kind != "word":
        fail("字段缺少类型")
    start = index
    name = tokens[index].upper
    supported = name in SIMPLE_TYPES
    index += 1
    if name == "DOUBLE" and index < len(tokens) and tokens[index].upper == "PRECISION":
        index += 1
    dimensions = dict.fromkeys(DIMENSIONS)
    if index < len(tokens) and tokens[index].text == "(":
        end = closing_index(tokens, index)
        args = split_top(tokens[index + 1:end])
        if not all(len(arg) == 1 and arg[0].kind == "number" and arg[0].text.isdigit() for arg in args):
            supported = False
        else:
            numbers = [int(arg[0].text) for arg in args]
            if name in NUMERIC_TYPES and 1 <= len(numbers) <= 2:
                dimensions["precision"] = numbers[0]
                dimensions["scale"] = numbers[1] if len(numbers) == 2 else None
                if not numbers[0] or (len(numbers) == 2 and numbers[1] > numbers[0]):
                    fail("precision/scale 无效")
            elif name in CHAR_TYPES and len(numbers) == 1 and numbers[0] > 0:
                dimensions["length"] = numbers[0]
            else:
                supported = False
        index = end + 1
    if index < len(tokens) and tokens[index].upper == "UNSIGNED":
        index += 1
    return canonical(tokens[start:index], upper=True), dimensions, index, supported


def parse_create(tokens: list[Token], loc, field_loc) -> dict:
    index = 1
    if index < len(tokens) and tokens[index].upper in {"TEMP", "TEMPORARY"}:
        index += 1
    if index >= len(tokens) or tokens[index].upper != "TABLE":
        return unsupported(new_statement("unsupported", "", loc), "仅支持 CREATE TABLE", field_set=True)
    index += 1
    if [t.upper for t in tokens[index:index + 3]] == ["IF", "NOT", "EXISTS"]:
        index += 3
    table_start = index
    table, index = qualified(tokens, index)
    st = new_statement("create", table, loc)
    set_table(st, tokens, table_start)
    if index >= len(tokens) or tokens[index].text != "(":
        if index < len(tokens) and tokens[index].upper in {"AS", "LIKE"}:
            return unsupported(st, "CREATE AS/LIKE 不支持", field_set=True)
        fail("CREATE TABLE 缺少表体", loc)
    end = closing_index(tokens, index)
    entries = split_top(tokens[index + 1:end])
    constraints = []
    field_names = set()
    for entry in entries:
        part = entry
        if part[0].upper == "CONSTRAINT":
            if len(part) < 3:
                fail("CONSTRAINT 定义不完整", field_loc(part[0]))
            identifier(part[1])
            part = part[2:]
        if part[0].upper in {"PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "KEY", "INDEX"}:
            constraints.append(part)
            continue
        field = identifier(part[0])
        if field in field_names:
            fail("CREATE TABLE 存在重复字段", field_loc(part[0]))
        field_names.add(field)
        typ, dims, offset, type_supported = consume_type(part, 1)
        row = field_record(table, field, "create", field_loc(part[0]))
        row["schema"] = st["schema"]
        row.update({"type": typ, **dims, "nullable": True, "default": "", "primary_key": False,
                    "unique": False, "auto_increment": False, "comment": "", "foreign_table": "", "foreign_field": ""})
        row["known"] = list(ATTRS)
        if not type_supported:
            for key in ("type", "length", "precision", "scale"):
                row["known"].remove(key)
            issue(st, "UNSUPPORTED_TYPE", "类型参数或类型不在支持范围", fields=[field], location=row["location"])
        seen = set()
        while offset < len(part):
            token = part[offset]
            key = token.upper
            if key == "NOT" and offset + 1 < len(part) and part[offset + 1].upper == "NULL":
                attribute, value, step = "nullable", False, 2
            elif key == "NULL":
                attribute, value, step = "nullable", True, 1
            elif key == "PRIMARY" and offset + 1 < len(part) and part[offset + 1].upper == "KEY":
                attribute, value, step = "primary_key", True, 2
            elif key == "UNIQUE":
                attribute, value, step = "unique", True, 1
            elif key in {"AUTO_INCREMENT", "AUTOINCREMENT"}:
                attribute, value, step = "auto_increment", True, 1
            elif key == "COMMENT" and offset + 1 < len(part) and part[offset + 1].kind == "string":
                attribute, value, step = "comment", part[offset + 1].text[1:-1].replace("''", "'"), 2
            elif key == "DEFAULT":
                start = offset + 1
                stop = len(part)
                boundaries = {"NOT", "NULL", "PRIMARY", "UNIQUE", "COMMENT", "REFERENCES", "CHECK", "CONSTRAINT", "COLLATE", "GENERATED", "AUTO_INCREMENT", "AUTOINCREMENT"}
                for j, t, depth in levels(part[start:]):
                    if j > 0 and not depth and t.upper in boundaries:
                        stop = start + j
                        break
                expression = part[start:stop]
                if not expression:
                    fail("DEFAULT 缺少值", row["location"])
                attribute, value, step = "default", canonical(expression, upper=True), stop - offset
                valid, references = inspect_expression(expression)
                if not valid or references:
                    row["known"].remove("default")
                    issue(st, "UNSUPPORTED_DEFAULT", "默认值表达式不支持或引用外部字段", fields=[field], location=row["location"])
            else:
                # Preserve parsed fields, but never claim complete declaration coverage.
                unsupported(st, "字段修饰/约束不支持: " + canonical(part[offset:]))
                row["known"] = [k for k in row["known"] if k in {"type", "length", "precision", "scale"} | seen]
                break
            if attribute in seen:
                fail("字段声明重复或冲突: " + attribute, row["location"])
            seen.add(attribute)
            row[attribute] = value
            offset += step
        if row["primary_key"]:
            if st["primary_key_fields"]:
                fail("多个字段级 PRIMARY KEY；复合主键请使用表级声明", row["location"])
            st["primary_key_fields"].append(field)
        st["fields"].append(row)
    by_field = {f["field"]: f for f in st["fields"]}
    for part in constraints:
        key = part[0].upper
        offset = 2 if key == "PRIMARY" and len(part) > 1 and part[1].upper == "KEY" else 1
        if key not in {"PRIMARY", "UNIQUE"}:
            unsupported(st, "表级约束不支持: " + canonical(part))
            for row in st["fields"]:
                for attr in ("foreign_table", "foreign_field", "primary_key", "unique"):
                    if attr in row["known"]:
                        row["known"].remove(attr)
            st["primary_key_order_known"] = False
            continue
        if offset >= len(part) or part[offset].text != "(":
            unsupported(st, "表级约束仅支持 PRIMARY KEY (...) / UNIQUE (...)")
            attr = "primary_key" if key == "PRIMARY" else "unique"
            for row in st["fields"]:
                if attr in row["known"]:
                    row["known"].remove(attr)
            if key == "PRIMARY":
                st["primary_key_order_known"] = False
            continue
        close = closing_index(part, offset)
        names = []
        for group in split_top(part[offset + 1:close]):
            if len(group) != 1:
                unsupported(st, "约束列表达式不支持")
                attr = "primary_key" if key == "PRIMARY" else "unique"
                for row in st["fields"]:
                    if attr in row["known"]:
                        row["known"].remove(attr)
                if key == "PRIMARY":
                    st["primary_key_order_known"] = False
                continue
            name = identifier(group[0])
            if name not in by_field or name in names:
                fail("约束引用不存在或重复字段", loc)
            names.append(name)
        if close != len(part) - 1:
            unsupported(st, "表级约束尾部不支持")
        if key == "PRIMARY":
            if st["primary_key_fields"]:
                fail("重复 PRIMARY KEY 声明", loc)
            st["primary_key_fields"] = names
        if key == "UNIQUE" and len(names) != 1:
            issue(st, "COMPOSITE_UNIQUE", "复合 UNIQUE 不能等同各字段 UNIQUE", fields=names)
            st.setdefault("constraints", []).append({"kind": "unique", "fields": names})
            for name in names:
                if "unique" in by_field[name]["known"]:
                    by_field[name]["known"].remove("unique")
        else:
            for name in names:
                by_field[name]["primary_key" if key == "PRIMARY" else "unique"] = True
    if end != len(tokens) - 1:
        unsupported(st, "CREATE TABLE 尾部选项不支持: " + canonical(tokens[end + 1:]))
    if not st["fields"]:
        fail("CREATE TABLE 没有字段", loc)
    st["primary_key_order_known"] = st["primary_key_order_known"] and all("primary_key" in f["known"] for f in st["fields"])
    return st


def query_metadata(st: dict):
    for row in st["fields"]:
        issue(st, "UNDECLARED", "SELECT/INSERT 不声明类型或约束，不推断", fields=[row["field"]], location=row["location"])
        row["unknown_attributes"] = list(QUERY_UNKNOWN)


def parse_select(tokens: list[Token], loc, field_loc) -> dict:
    st = new_statement("select", "", loc)
    if any(t.upper in {"SELECT", "UNION", "INTERSECT", "EXCEPT", "JOIN", "APPLY", "INTO"} for t in tokens[1:]):
        return unsupported(st, "CTE/JOIN/子查询/集合运算/SELECT INTO 不支持", field_set=True)
    from_index = next((i for i, t, d in levels(tokens) if not d and t.upper == "FROM"), None)
    if from_index is None:
        return unsupported(st, "仅支持带单表 FROM 的 SELECT", field_set=True)
    start = 2 if len(tokens) > 1 and tokens[1].upper in {"DISTINCT", "ALL"} else 1
    if start == from_index:
        fail("SELECT 投影为空", loc)
    if from_index + 1 >= len(tokens):
        fail("FROM 缺少表", loc)
    if tokens[from_index + 1].text == "(":
        return unsupported(st, "FROM 子查询不支持", field_set=True)
    table, end = qualified(tokens, from_index + 1)
    st["table"] = table
    set_table(st, tokens, from_index + 1)
    alias = None
    if end < len(tokens) and tokens[end].upper == "AS":
        if end + 1 >= len(tokens):
            fail("表别名缺少名称", loc)
        alias = identifier(tokens[end + 1])
        end += 2
    elif end < len(tokens) and tokens[end].kind in {"word", "identifier"} and tokens[end].upper not in {"WHERE", "GROUP", "HAVING", "ORDER", "LIMIT", "OFFSET", "FETCH", "FOR"}:
        alias = identifier(tokens[end])
        end += 1
    if end < len(tokens):
        # We don't validate predicate/order dialects; parsed projections are partial evidence.
        if any(t.text == "," and not d for _, t, d in levels(tokens[end:])) and tokens[end].text == ",":
            return unsupported(st, "多表 FROM 不支持", field_set=True)
        unsupported(st, "SELECT 尾部子句不在验证范围: " + canonical(tokens[end:]))
    for group in split_top(tokens[start:from_index]):
        expr = group
        output_alias = None
        top_as = [i for i, t, d in levels(group) if not d and t.upper == "AS"]
        if top_as:
            i = top_as[-1]
            if i != len(group) - 2 or not i:
                fail("投影 AS 别名格式无效", field_loc(group[0]))
            output_alias = identifier(group[-1])
            expr = group[:i]
        elif len(group) >= 2 and group[-1].kind in {"word", "identifier"} and group[-2].text not in {".", "+", "-", "*", "/", "%", "=", "|", ">", "<"} and group[-1].start > group[-2].end:
            # Column, qualified column, function or literal implicit alias.
            simple_prefix = False
            if group[0].kind in {"word", "identifier"}:
                try:
                    _, consumed = qualified(group[:-1])
                    simple_prefix = consumed == len(group) - 1
                except PluginError:
                    pass
            if len(group) == 2 or group[-2].text == ")" or simple_prefix:
                output_alias = identifier(group[-1])
                expr = group[:-1]
        source = None
        source_parts = None
        try:
            if expr[0].kind in {"word", "identifier"}:
                candidate, consumed = qualified(expr)
                if consumed == len(expr):
                    source = candidate
                    source_parts, _ = qualified_parts(expr)
        except PluginError:
            pass
        wildcard = any(t.text == "*" for t in expr) and (len(expr) == 1 or (len(expr) >= 3 and expr[-2].text == "."))
        if wildcard:
            row = field_record(table, output_alias or "*", "select", field_loc(group[0]))
            row["schema"] = st["schema"]
            row["expression"] = canonical(expr)
            st["fields"].append(row)
            st["field_set_complete"] = False
            issue(st, "WILDCARD", "SELECT * 无法在离线模式展开", location=row["location"])
            continue
        if source_parts and len(source_parts) > 1:
            prefix = ".".join(source_parts[:-1])
            if prefix not in {table, table.rsplit(".", 1)[-1], alias}:
                unsupported(st, "来源字段限定符不是当前单表/别名", field_set=True)
        valid_expression, source_fields = inspect_expression(expr)
        if not valid_expression:
            unsupported(st, "投影表达式不在语法验证范围")
        for reference in source_fields:
            if "." in reference and reference.rsplit(".", 1)[0] not in {table, table.rsplit(".", 1)[-1], alias}:
                unsupported(st, "表达式来源字段限定符不是当前单表/别名")
        if expr[-1].text in {"+", "-", "/", ".", "=", "|"}:
            fail("投影表达式不完整", field_loc(group[0]))
        field = output_alias or (source_parts[-1] if source_parts else canonical(expr))
        row = field_record(table, field, "select", field_loc(group[0]))
        row.update({"schema": st["schema"], "alias": output_alias, "expression": canonical(expr), "source_field": source,
                    "source_fields": source_fields, "expression_supported": valid_expression,
                    "known": ["expression", "alias", "source_field", "source_fields"]})
        st["fields"].append(row)
    query_metadata(st)
    return st


def parse_insert(tokens: list[Token], loc, field_loc) -> dict:
    st = new_statement("insert", "", loc)
    if len(tokens) < 3 or tokens[1].upper != "INTO":
        return unsupported(st, "仅支持 INSERT INTO", field_set=True)
    table, index = qualified(tokens, 2)
    st["table"] = table
    set_table(st, tokens, 2)
    columns = []
    column_names = set()
    if index < len(tokens) and tokens[index].text == "(":
        close = closing_index(tokens, index)
        for group in split_top(tokens[index + 1:close]):
            if len(group) != 1:
                return unsupported(st, "INSERT columns 仅允许简单字段名", field_set=True)
            name = identifier(group[0])
            if name in column_names:
                fail("INSERT columns 重复", field_loc(group[0]))
            column_names.add(name)
            columns.append(name)
            row = field_record(table, name, "insert", field_loc(group[0]))
            row.update({"schema": st["schema"], "source_field": name, "source_fields": [name], "known": ["source_field", "source_fields"]})
            st["fields"].append(row)
        index = close + 1
    if index >= len(tokens):
        fail("INSERT 缺少 VALUES", loc)
    if tokens[index].upper != "VALUES":
        return unsupported(st, "仅支持 INSERT ... VALUES，不支持 INSERT SELECT/SET/DEFAULT", field_set=not bool(columns))
    index += 1
    count = 0
    arity = len(columns) if columns else None
    while index < len(tokens) and tokens[index].text == "(":
        close = closing_index(tokens, index)
        values = split_top(tokens[index + 1:close])
        if arity is None:
            arity = len(values)
        if len(values) != arity:
            fail("INSERT VALUES 数量与列数/其它行不一致", field_loc(tokens[index]))
        if not all(inspect_expression(group)[0] for group in values):
            unsupported(st, "INSERT VALUES 表达式/子查询不在验证范围")
        if any(group[-1].text in {"+", "-", "/", ".", "="} for group in values):
            fail("INSERT VALUES 表达式不完整", field_loc(tokens[index]))
        count += 1
        index = close + 1
        if index < len(tokens) and tokens[index].text == ",":
            index += 1
            if index >= len(tokens) or tokens[index].text != "(":
                fail("INSERT 多行 VALUES 缺少下一行", loc)
        else:
            break
    if not count:
        fail("INSERT VALUES 缺少值列表", loc)
    st["value_rows"] = count
    if index < len(tokens):
        unsupported(st, "INSERT VALUES 尾部选项不支持")
    if not columns:
        st["field_set_complete"] = False
        issue(st, "INSERT_COLUMNS_UNKNOWN", "INSERT 未显式提供 columns，无法从值推断字段或类型")
    query_metadata(st)
    return st


def parse_sql(text: str, location: dict) -> list[dict]:
    tokens = tokenize(text, location)
    if not tokens:
        fail("SQL 文本为空或仅包含注释", location)
    lines = [-1] + [i for i, char in enumerate(text) if char == "\n"]

    def token_loc(token):
        line = bisect_right(lines, token.start)
        return {**location, "sql_line": line, "sql_column": token.start - lines[line - 1], "offset": token.start}

    chunks, start = [], 0
    for i, token in enumerate(tokens):
        if token.text == ";":
            if i > start:
                chunks.append(tokens[start:i])
            start = i + 1
    if start < len(tokens):
        chunks.append(tokens[start:])
    if len(chunks) > MAX_STATEMENTS:
        raise PluginError("INPUT_LIMIT_EXCEEDED", "语句数量超过限制")
    result = []
    for chunk in chunks:
        loc = token_loc(chunk[0])
        loc["statement"] = len(result) + 1

        def field_loc(token):
            return {**token_loc(token), "statement": loc["statement"]}

        if any(t.kind == "unsupported" for t in chunk):
            st = unsupported(new_statement("unsupported", "", loc), "不支持 executable comment / dollar quote / 方言相关反斜杠转义", field_set=True)
        elif chunk[0].upper == "CREATE":
            st = parse_create(chunk, loc, field_loc)
        elif chunk[0].upper == "SELECT":
            st = parse_select(chunk, loc, field_loc)
        elif chunk[0].upper == "INSERT":
            st = parse_insert(chunk, loc, field_loc)
        else:
            st = unsupported(new_statement("unsupported", "", loc), "不支持语句: " + (chunk[0].upper or chunk[0].text), field_set=True)
        st["index"] = len(result) + 1
        result.append(st)
    return result


def structure_value(key: str, value: Any, location: dict):
    if key in BOOLS:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0", "yes", "no"}:
            return value.strip().lower() in {"true", "1", "yes"}
        if type(value) is int and value in (0, 1):
            return bool(value)
        fail("结构清单布尔属性无效: " + key, location)
    if key in DIMENSIONS:
        if value is None or value == "":
            return None
        if type(value) is int and value >= 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        fail("结构清单维度属性无效: " + key, location)
    if value is None and key in {"default", "comment", "foreign_table", "foreign_field"}:
        return ""
    if not isinstance(value, str):
        fail("结构清单属性必须为字符串: " + key, location)
    if key == "type":
        return value.strip()
    return value


def parse_structure(dataset: dict) -> list[dict]:
    # Mandatory type distinguishes a declared field list from ordinary table data.
    if not {"table", "field", "type"} <= set(dataset["columns"]):
        raise PluginError("INPUT_INVALID", "structure 模式需要明确声明 table、field、type 列；普通业务数据不能作为结构")
    statements: dict[str, dict] = {}
    field_names: dict[str, set] = {}
    for index, source in enumerate(dataset["rows"]):
        loc = dataset["locations"][index]
        for key in ("table", "field", "type"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                raise PluginError("INPUT_INVALID", "结构清单 table、field、type 必须是非空字符串", details={"location": loc})
        table, field = source["table"].strip(), source["field"].strip()
        if table not in statements:
            statements[table] = new_statement("structure", table, loc)
            field_names[table] = set()
        st = statements[table]
        if field in field_names[table]:
            fail("结构清单同表字段重复", loc)
        field_names[table].add(field)
        row = field_record(table, field, "structure", loc)
        for key in ATTRS:
            if key in source and not (source[key] in ("", None) and key in BOOLS):
                row[key] = structure_value(key, source[key], loc)
                row["known"].append(key)
        type_tokens = tokenize(row["type"], loc)
        typ, dimensions, end, supported = consume_type(type_tokens, 0)
        row["type"] = typ
        if not supported or end != len(type_tokens):
            row["known"] = [k for k in row["known"] if k != "type"]
            issue(st, "UNSUPPORTED_TYPE", "清单类型不在验证范围", fields=[field], location=loc)
        for key, value in dimensions.items():
            if key in row["known"] and row[key] != value:
                fail("结构清单 type 与维度属性冲突: " + key, loc)
            # Type parameters are declarations; missing independent dimension cells can be derived.
            row[key] = value
            if key not in row["known"] and supported:
                row["known"].append(key)
        if "default" in row["known"] and row["default"]:
            row["default"] = canonical(tokenize(row["default"], loc), upper=True)
        missing = [key for key in ATTRS if key not in row["known"]]
        if missing:
            issue(st, "MISSING_DECLARATION", "清单未声明属性: " + ", ".join(missing), fields=[field], location=loc)
            row["unknown_attributes"] = missing
        # Legacy sql.parse columns for partition/etc are not quietly treated as fully covered.
        ignored = set(source) - set(ATTRS) - {"table", "field", "table_comment", "dialect"}
        for key in ignored:
            if source[key] not in (None, "", False):
                issue(st, "UNSUPPORTED_ATTRIBUTE", "清单属性不支持: " + key, fields=[field], location=loc)
        st["fields"].append(row)
    if not statements:
        raise PluginError("INPUT_INVALID", "结构清单没有声明字段")
    for st in statements.values():
        primary = [f["field"] for f in st["fields"] if "primary_key" in f["known"] and f["primary_key"]]
        st["primary_key_fields"] = primary
        st["primary_key_order_known"] = len(primary) <= 1 and all("primary_key" in f["known"] for f in st["fields"])
        if len(primary) > 1:
            issue(st, "COMPOSITE_PRIMARY_ORDER", "sql.parse 清单仅保留主键成员，不能确认复合主键顺序", fields=primary)
    return list(statements.values())


def load_side(params: dict, file_key: str, text_key: str, options_key: str, mode_key: str, column: str) -> dict:
    present = [key for key in (file_key, text_key) if key in params]
    if len(present) != 1:
        raise PluginError("INVALID_PARAMS", f"{file_key}/{text_key} 必须分别恰好提供一种（按键存在判断）")
    key = present[0]
    if not isinstance(params[key], str) or not params[key].strip():
        raise PluginError("INVALID_PARAMS", key + " 必须是非空字符串")
    options = params.get(options_key, {})
    mode = params.get(mode_key, "sql")
    if not isinstance(options, dict) or mode not in {"sql", "structure"}:
        raise PluginError("INVALID_PARAMS", "options 必须是对象，mode 必须为 sql/structure")
    if key == text_key:
        if mode != "sql":
            raise PluginError("INVALID_PARAMS", "text 仅支持 sql 模式，声明结构清单请通过文件输入")
        if set(options) - {"format"} or options.get("format", "sql") not in {"auto", "sql"}:
            raise PluginError("INVALID_PARAMS", "直接 SQL text 不适用表格 options；只允许 format=auto/sql")
        dataset = {"columns": ["sql"], "rows": [{"sql": params[key]}],
                   "locations": [{"source": key, "row": 1}], "format": "sql",
                   "source": key, "warnings": [], "complete": True}
    else:
        dataset = read_dataset(params[key], options)
    if mode == "structure":
        statements = parse_structure(dataset)
    else:
        sql_column = "sql" if dataset["format"] == "sql" else column
        if sql_column not in dataset["columns"]:
            raise PluginError("INPUT_INVALID", "数据没有 SQL 文本列: " + sql_column)
        statements = []
        for index, row in enumerate(dataset["rows"]):
            text = row.get(sql_column)
            if not isinstance(text, str) or not text.strip():
                raise PluginError("INPUT_INVALID", "SQL 列必须包含非空字符串", details={"location": dataset["locations"][index]})
            location = {**dataset["locations"][index], "column": sql_column}
            try:
                statements.extend(parse_sql(text, location))
            except PluginError as error:
                if error.code == "SQL_PARSE_ERROR" and not error.details.get("location"):
                    error.details["location"] = location
                raise
            if len(statements) > MAX_STATEMENTS:
                raise PluginError("INPUT_LIMIT_EXCEEDED", "总语句数超过限制")
    if not statements:
        raise PluginError("INPUT_INVALID", "输入没有可检查记录")
    fields_count = sum(len(st["fields"]) for st in statements)
    if fields_count > MAX_FIELDS:
        raise PluginError("INPUT_LIMIT_EXCEEDED", "总结构字段数超过限制")
    unknown = [u for st in statements for u in st["unknown"]]
    if not dataset["complete"]:
        unknown.append({"code": "INPUT_INCOMPLETE", "message": "SDK 返回输入不完整", "location": {"source": dataset["source"]}, "fields": []})
    return {"mode": mode, "format": dataset["format"], "source": dataset["source"],
            "statements": statements, "complete": bool(dataset["complete"] and all(st["complete"] for st in statements)),
            "unknown": unknown, "warnings": list(dataset["warnings"]) + [u["message"] for u in unknown],
            "field_count": fields_count}


def compare(left: dict, right: dict) -> dict:
    differences, unknown = [], [{"side": side, **u} for side, model in (("left", left), ("right", right)) for u in model["unknown"]]

    def difference(code, l, r, **details):
        differences.append({"code": code, "left_location": l["location"] if l else None,
                            "right_location": r["location"] if r else None, **details})

    lstat, rstat = left["statements"], right["statements"]
    if len(lstat) != len(rstat):
        if all(not s["unsupported"] for s in lstat + rstat):
            difference("STATEMENT_COUNT", lstat[0], rstat[0], left=len(lstat), right=len(rstat))
        else:
            unknown.append({"code": "STATEMENT_ALIGNMENT", "message": "不支持语句存在，不能确认语句集合差异", "location": {}})
    for l, r in zip(lstat, rstat):
        if not l["table"] or not r["table"]:
            continue
        if l["table"] != r["table"]:
            difference("TABLE", l, r, left=l["table"], right=r["table"])
        elif "table_parts" in l and "table_parts" in r and l["table_parts"] != r["table_parts"]:
            difference("TABLE_QUALIFICATION", l, r, left=l["table_parts"], right=r["table_parts"])
        elif ("table_parts" in l and any("." in p for p in l["table_parts"])) or ("table_parts" in r and any("." in p for p in r["table_parts"])):
            if l["kind"] == "structure" or r["kind"] == "structure":
                unknown.append({"code": "TABLE_QUALIFICATION_UNKNOWN", "message": "清单未保留引用标识符与限定层级的区别", "left_location": l["location"], "right_location": r["location"]})
        if l["primary_key_order_known"] and r["primary_key_order_known"] and l["primary_key_fields"] != r["primary_key_fields"]:
            difference("PRIMARY_KEY_ORDER", l, r, left=l["primary_key_fields"], right=r["primary_key_fields"])
        lf, rf = l["fields"], r["fields"]
        lnames, rnames = [f["field"] for f in lf], [f["field"] for f in rf]
        # Wildcards and unknown field sets cannot prove missing/extra columns.
        if l["field_set_complete"] and r["field_set_complete"]:
            if lnames != rnames:
                difference("FIELDS_OR_ORDER", l, r, left=lnames, right=rnames)
        elif lnames != rnames:
            unknown.append({"code": "FIELD_SET_UNKNOWN", "message": "字段集合无法展开，不判断缺失/新增", "left_location": l["location"], "right_location": r["location"]})
        rby = {f["field"]: f for f in rf}
        if len(rby) != len(rf) or len(set(lnames)) != len(lf):
            unknown.append({"code": "AMBIGUOUS_PROJECTION", "message": "重复投影名不能唯一对齐", "left_location": l["location"], "right_location": r["location"]})
            continue
        for a in lf:
            b = rby.get(a["field"])
            if not b or a["field"] == "*":
                continue
            for attr in ATTRS:
                if attr in a["known"] and attr in b["known"] and a[attr] != b[attr]:
                    difference("ATTRIBUTE", a, b, table=a["table"], field=a["field"], attribute=attr, left=a[attr], right=b[attr])
            if l["kind"] == r["kind"] == "select":
                for attr in ("expression", "alias", "source_field", "source_fields"):
                    if attr in a["known"] and attr in b["known"] and a[attr] != b[attr]:
                        difference("PROJECTION", a, b, table=a["table"], field=a["field"], attribute=attr, left=a[attr], right=b[attr])
    complete = left["complete"] and right["complete"] and not unknown
    verdict = "different" if differences else ("equal" if complete else "inconclusive")
    return {"verdict": verdict, "complete": bool(complete), "differences": differences,
            "unknown": unknown, "warnings": left["warnings"] + right["warnings"],
            "comparison_policy": "strict_declared; compare known overlapping attributes; unknown is neither equal nor different; statement and field order are significant",
            "left": left, "right": right}


def bounded(value: Any, *, max_string: int = 2000) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_string else value[:max_string] + "… [display truncated; full report in JSON]"
    if isinstance(value, list):
        return [bounded(item, max_string=max_string) for item in value[:DISPLAY_LIMIT]]
    if isinstance(value, dict):
        return {key: bounded(item, max_string=max_string) for key, item in value.items()}
    return value


def side_summary(model: dict) -> dict:
    return {"mode": model["mode"], "format": model["format"], "source": model["source"],
            "complete": model["complete"], "statement_count": len(model["statements"]),
            "field_count": model["field_count"], "unknown_count": len(model["unknown"]),
            "unknown": bounded(model["unknown"]), "unknown_truncated": len(model["unknown"]) > DISPLAY_LIMIT,
            "warnings": bounded(model["warnings"]), "warnings_truncated": len(model["warnings"]) > DISPLAY_LIMIT}


def markdown(report: dict, task_id: str) -> str:
    # JSON is complete evidence; Markdown is bounded to avoid huge GUI/report views.
    lines = ["# SQL 结构检查", "", f"任务：{task_id}", "", "仅解析本地文本；不执行 SQL、不连接数据库。", "",
             f"结论：{report.get('verdict', 'preview')}", f"完整：{report['complete']}", "",
             "unknown 不代表不同，也不能作为一致证明。声明值/标识符按文本比较，不宣称方言或表达式语义等价。", ""]
    for name in ("left", "right", "structure"):
        model = report.get(name)
        if not model:
            continue
        lines.extend([f"## {name}", "", "```json", json.dumps(bounded(side_summary(model)), ensure_ascii=False, indent=2), "```", ""])
        sample = [field for st in model["statements"] for field in st["fields"]][:20]
        lines.extend(["### 结构样例与来源（最多 20 行）", "", "```json", json.dumps(bounded(sample), ensure_ascii=False, indent=2), "```", ""])
    for key in ("differences", "unknown", "warnings"):
        values = report.get(key, [])
        lines.extend([f"## {key}（共 {len(values)}；最多展示 {DISPLAY_LIMIT}）", "", "```json", json.dumps(bounded(values), ensure_ascii=False, indent=2), "```", ""])
    lines.extend(["完整结构、来源位置和全部差异/未知见同任务 ID 的 JSON 产物。", ""])
    return "\n".join(lines)


class Plugin:
    def init(self, context):
        self.context = context

    def execute(self, command, params):
        batch_result = run_dataset_batch(self, command, params)
        if batch_result is not None:
            return batch_result
        return self.execute_one(command, {k: v for k, v in params.items()
                                         if k not in {"inputs", "left_inputs", "right_inputs", "batch"}})

    def execute_one(self, command: str, params: dict) -> Result:
        column = params.get("sql_column", "sql")
        if not isinstance(column, str) or not column.strip():
            raise PluginError("INVALID_PARAMS", "sql_column 必须是非空字符串")
        if command == "sql.diff":
            # Validate BOTH exactly-one choices before reading either side.
            for f, t in (("left", "left_text"), ("right", "right_text")):
                if sum(key in params for key in (f, t)) != 1:
                    raise PluginError("INVALID_PARAMS", f"{f}/{t} 必须恰好提供一种")
            left = load_side(params, "left", "left_text", "left_options", "left_mode", column)
            right = load_side(params, "right", "right_text", "right_options", "right_mode", column)
            report = compare(left, right)
            data = {key: report[key] for key in ("verdict", "complete", "comparison_policy")}
            for key in ("differences", "unknown", "warnings"):
                data[key] = bounded(report[key])
                data[key + "_count"] = len(report[key])
                data[key + "_truncated"] = len(report[key]) > DISPLAY_LIMIT
            data.update({"left": side_summary(left), "right": side_summary(right),
                         "display_bounded": True, "display_string_limit": 2000})
            message = "结构比对：" + report["verdict"]
        elif command == "sql.preview":
            sample = params.get("sample_rows", 20)
            if type(sample) is not int or not 0 <= sample <= 100:
                raise PluginError("INVALID_PARAMS", "sample_rows 必须是 0 至 100 的整数")
            model = load_side(params, "input", "text", "options", "input_mode", column)
            all_rows = [row for st in model["statements"] for row in st["fields"]]
            columns = ["table", "schema", "field", "type", "kind", "declared", "source_field", "source_fields", "alias", "expression", *[a for a in ATTRS if a != "type"], "known", "location"]
            data = {**side_summary(model), "columns": columns, "rows": bounded(all_rows[:sample]),
                    "sample_truncated": len(all_rows) > sample, "sample_count": min(sample, len(all_rows)),
                    "total_rows": len(all_rows), "structure": bounded([{k: v for k, v in st.items() if k != "fields"} for st in model["statements"][:DISPLAY_LIMIT]]),
                    "structure_truncated": len(model["statements"]) > DISPLAY_LIMIT,
                    "display_bounded": True, "display_string_limit": 2000}
            report = {"complete": model["complete"], "structure": model, "unknown": model["unknown"], "warnings": model["warnings"]}
            message = f"已预览 {data['sample_count']}/{len(all_rows)} 个结构字段；完整={model['complete']}"
        else:
            raise PluginError("UNKNOWN_COMMAND", "不支持的命令")
        task_id = self.context.task.id
        report["task_id"] = task_id
        files = [self.context.files.write_text(task_id + ".json", json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"),
                 self.context.files.write_text(task_id + ".md", markdown(report, task_id))]
        warnings = bounded(report["warnings"])
        if len(report["warnings"]) > DISPLAY_LIMIT:
            warnings.append("警告展示已截断；完整信息见 JSON 产物")
        return Result("success", message, data=data, files=files, warnings=warnings)

    def destroy(self):
        pass
