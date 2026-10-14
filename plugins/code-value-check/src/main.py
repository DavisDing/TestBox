from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from testbox.sdk import PluginError, Result, read_dataset


PLAN_COLUMNS = {
    "source_table": "源表名", "source_code_field": "源字段名",
    "target_table": "目标表", "target_code_field": "目标字段名", "domain_value": "码值域",
}
MAPPING_COLUMNS = {
    "domain": "码值域", "source_code": "源码值", "target_code": "目标码值", "code_label": "码值中文",
}
CASE_FIELDS = {
    "case_id", "source_table", "source_code_field", "target_table", "target_code_field", "domain_value",
    "source_keys", "target_keys", "mapping_conditions", "source_filters", "target_filters",
    "types", "nulls_equal", "enabled", "description",
}
CONFIG_KEYS = {
    "test_plan_input", "test_plan_options", "test_plan_columns", "mapping_mode", "mapping_source",
    "mapping_input", "mapping_options", "mapping_columns", "file_layout", "code_table", "defaults",
    "sql_engine", "engine_version", "output_mode", "statement_layout", "union_batch_size",
    "result_scope", "store_scope", "include_summary", "include_reverse_check", "result_table",
    "result_columns", "create_policy", "run_id", "duplicate_case_policy", "allow_mapping_null",
    "null_token", "numeric_text_policy", "max_sql_bytes",
}
READ_KEYS = {
    "format", "encoding", "delimiter", "quotechar", "escapechar", "sheet", "header_row", "start_row",
    "excel_range", "json_path", "has_header", "columns", "max_rows", "max_columns", "max_bytes",
    "max_cells", "max_cell_length", "skip_empty_rows", "formula_mode",
}
TEXT_TYPES = {"text", "numeric"}


def fail(reason, code="INVALID_PARAMS", location=None):
    prefix = ""
    if location:
        prefix = f"Sheet={location.get('sheet', '-')}, 行={location.get('row', '-')}："
    raise PluginError(code, prefix + reason, details={"location": location} if location else None)


def object_keys(value, keys, reason):
    if not isinstance(value, dict) or set(value) - keys:
        fail(reason)
    return value


def identifier(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 128 or any(ord(character) < 32 for character in value)):
        fail("标识符必须为非空纯列名/对象名（不含控制字符）")
    if any(character in value for character in "'\"`;()[]"):
        fail("标识符不能包含引用字符或 SQL 表达式")
    return value


def parts(value):
    values = value if isinstance(value, list) else value.split(".") if isinstance(value, str) else []
    if not 1 <= len(values) <= 3:
        fail("对象名须为1～3个标识符段")
    return [identifier(item) for item in values]


def field_list(value):
    if isinstance(value, str):
        if value.startswith("["):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                fail("组合键单元格须为 JSON 数组")
        else:
            value = [value]
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        fail("业务键须含1～16个字段")
    result = [identifier(item) for item in value]
    if len(set(result)) != len(result):
        fail("业务键字段重复")
    return result


def decoded(value, kind, location):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            fail("配置单元格须为合法 JSON", location=location)
    if not isinstance(value, kind):
        fail("配置单元格类型不正确", location=location)
    return value


def boolean(value, location=None):
    if type(value) is bool:
        return value
    if value in ("true", "TRUE", "是", "启用"):
        return True
    if value in ("false", "FALSE", "否", "停用"):
        return False
    fail("布尔配置须显式 true/false", location=location)


def value_type(value, kind, params, nullable=False, location=None):
    if value is None or (params.get("null_token") is not None and value == params["null_token"]):
        if nullable:
            return None
        fail("必填码值/维度不能为空", location=location)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        fail("码值仅支持明确的文本或有限数字", location=location)
    if kind == "text":
        if not isinstance(value, str):
            if params.get("numeric_text_policy", "reject") != "convert":
                fail("文本码值遇到数字单元格，须显式 numeric_text_policy=convert", location=location)
            value = str(value)
        if not value:
            fail("空白码值无明确业务含义", location=location)
        return value
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        fail("numeric 码值不是合法数字", location=location)
    if not number.is_finite() or abs(number.adjusted()) > 100:
        fail("numeric 码值必须为有界有限数字", location=location)
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if number == 0:
        rendered = "0"
    integer, _, fraction = rendered.lstrip("-").partition(".")
    if len(integer.lstrip("0")) > 20 or len(fraction) > 18 or len(rendered) > 42:
        fail("numeric 常量超过 DECIMAL(38,18) 可表达范围", location=location)
    return rendered


def conditions(value, dynamic=True):
    if not isinstance(value, list) or len(value) > 50:
        fail("条件须为数组，最多50条")
    result = []
    for original in value:
        allowed = {"mapping_field", "field", "operator", "value_source", "value", "value_type"}
        item = dict(object_keys(original, allowed, "条件含未知配置"))
        operator = item.get("operator")
        if operator not in {"eq", "ne", "in", "is_null", "is_not_null"}:
            fail("不支持该运算符（OR/非等值字段关联不支持）")
        left = "mapping_field" if dynamic else "field"
        identifier(item.get(left))
        if operator in {"is_null", "is_not_null"}:
            if set(item) != {left, "operator"}:
                fail("空值条件不接收右值")
        else:
            source = item.get("value_source", "literal")
            if source not in ({"literal", "case_field", "source_field"} if dynamic else {"literal"}):
                fail("取值来源不支持")
            item["value_source"] = source
            if source == "literal":
                if set(item) - {left, "operator", "value_source", "value", "value_type"}:
                    fail("常量条件含无效右值字段")
                if item.get("value_type", "text") not in TEXT_TYPES:
                    fail("常量类型仅支持 text/numeric")
                item.setdefault("value_type", "text")
                values = item.get("value") if operator == "in" else [item.get("value")]
                if not isinstance(values, list) or not 1 <= len(values) <= 100:
                    fail("IN 须为非空有界数组")
                item["value"] = [value_type(entry, item["value_type"], {}) for entry in values]
                if operator != "in":
                    item["value"] = item["value"][0]
            else:
                if set(item) - {left, "operator", "value_source", "field"}:
                    fail("动态条件含无效配置")
                if operator != "eq" or "value" in item or "value_type" in item:
                    fail("动态/用例条件仅支持等值且不接受常量")
                if source == "case_field" and item.get("field") not in {
                    "source_table", "source_code_field", "target_table", "target_code_field", "domain_value",
                }:
                    fail("用例取值字段不在白名单")
                identifier(item.get("field"))
        result.append(item)
    return result


def read_input(path, options):
    object_keys(options, READ_KEYS, "读取配置含未知字段")
    if options.get("formula_mode", "formula") != "formula":
        fail("配置文件不允许公式缓存模式")
    dataset = read_dataset(path, options)
    if dataset["format"] not in {"xlsx", "xlsm", "csv", "tsv", "json"}:
        fail("本插件只读取 XLSX/XLSM/CSV/TSV/JSON")
    if dataset["format"] in {"xlsx", "xlsm"}:
        if any("活动工作表" in warning for warning in dataset["warnings"]):
            fail("多 Sheet 必须显式选择 sheet")
        for row, location in zip(dataset["rows"], dataset["locations"]):
            if any(isinstance(value, str) and value.startswith("=") for value in row.values()):
                fail("配置和码值必须为非公式纯值", location=location)
    return dataset


def column_mapping(value, allowed=None):
    if not isinstance(value, dict) or not value:
        fail("表头映射须为非空对象")
    if allowed is not None and set(value) - allowed:
        fail("表头映射含未知逻辑字段")
    for header in value.values():
        identifier(header)
    for logical in value:
        identifier(logical)
    if len(set(value.values())) != len(value):
        fail("同一逻辑关系不能将不同字段映射到同一表头")
    return value


def project(dataset, mapping):
    if set(mapping.values()) - set(dataset["columns"]):
        fail("输入缺少配置的表头")
    for row, location in zip(dataset["rows"], dataset["locations"]):
        if set(mapping.values()) - set(row):
            fail("输入行缺少已映射字段；显式NULL应填写null而不是缺键", location=location)
    return [{key: row[header] for key, header in mapping.items()} for row in dataset["rows"]]


def same_region(left, right):
    if left.get("sheet") != right.get("sheet"):
        return False
    if "excel_range" not in left or "excel_range" not in right:
        return True
    from openpyxl.utils.cell import range_boundaries
    first = range_boundaries(left["excel_range"])
    second = range_boundaries(right["excel_range"])
    return not (first[2] < second[0] or second[2] < first[0] or first[3] < second[1] or second[3] < first[1])


def load_cases(params):
    defaults = object_keys(params.get("defaults", {}), CASE_FIELDS - {"case_id", "description"}, "defaults 含未知字段")
    dataset = read_input(params["test_plan_input"], params.get("test_plan_options", {}))
    mapping = column_mapping(params.get("test_plan_columns", PLAN_COLUMNS), CASE_FIELDS)
    rows = project(dataset, mapping)
    if not rows or len(rows) > 100000:
        fail("清单为空或超过输入上限", "INPUT_LIMIT_EXCEEDED")
    cases, groups, identifiers = [], {}, set()
    merged_count = skipped = 0
    combined = params.get("file_layout", "separate_relations") == "combined_rows"
    for index, (row, location) in enumerate(zip(rows, dataset["locations"])):
        case = {**copy.deepcopy(defaults), **row}
        try:
            if not boolean(case.get("enabled", True), location):
                skipped += 1
                continue
            for key in {"source_table", "target_table"}:
                case[key] = parts(case.get(key))
            for key in {"source_code_field", "target_code_field"}:
                case[key] = identifier(case.get(key))
            case["source_keys"] = field_list(case.get("source_keys"))
            case["target_keys"] = field_list(case.get("target_keys"))
            if len(case["source_keys"]) != len(case["target_keys"]):
                fail("源目标组合键数量不一致")
            types = decoded(case.get("types", {}), dict, location)
            object_keys(types, {"source_code", "target_code", "domain", "keys", "dimensions"}, "types 含未知配置")
            for name in {"source_code", "target_code", "domain"}:
                types.setdefault(name, "text")
                if types[name] not in TEXT_TYPES:
                    fail("字段类型仅支持 text/numeric")
            types.setdefault("keys", ["text"] * len(case["source_keys"]))
            if (not isinstance(types["keys"], list) or len(types["keys"]) != len(case["source_keys"])
                    or any(kind not in TEXT_TYPES for kind in types["keys"])):
                fail("keys 类型须按键顺序配置 text/numeric")
            types.setdefault("dimensions", {})
            if not isinstance(types["dimensions"], dict) or any(kind not in TEXT_TYPES for kind in types["dimensions"].values()):
                fail("维度类型配置不正确")
            for dimension in types["dimensions"]:
                identifier(dimension)
            case["types"] = types
            case["domain_value"] = value_type(case.get("domain_value"), types["domain"], params)
            case["mapping_conditions"] = conditions(decoded(case.get("mapping_conditions", []), list, location))
            for condition in case["mapping_conditions"]:
                name = condition["mapping_field"]
                if name in {"domain", "source_code", "target_code"}:
                    if name in types["dimensions"] and types["dimensions"][name] != types[name]:
                        fail("核心字段维度声明与码值类型不一致")
                    types["dimensions"][name] = types[name]
                if condition.get("value_source") == "literal":
                    kind = types.get(name, types["dimensions"].get(name, condition["value_type"]))
                    if kind != condition["value_type"]:
                        fail("条件类型与映射维度声明不一致")
                    if name not in {"domain", "source_code", "target_code"}:
                        types["dimensions"][name] = kind
            for name in ("source_filters", "target_filters"):
                case[name] = conditions(decoded(case.get(name, []), list, location), False)
            case["nulls_equal"] = boolean(case.get("nulls_equal", False), location)
        except PluginError as error:
            fail(str(error), error.code, location)
        explicit_id = case.pop("case_id", None)
        if explicit_id is not None and (not isinstance(explicit_id, str) or not explicit_id.strip()
                                        or explicit_id != explicit_id.strip() or len(explicit_id) > 128):
            fail("case_id 须为非空文本", location=location)
        case.pop("description", None)
        case.pop("enabled", None)
        signature = json.dumps(case, ensure_ascii=False, sort_keys=True)
        group_key = (explicit_id or signature) if combined else (signature, explicit_id)
        if group_key in groups:
            previous = groups[group_key]
            if combined and previous["signature"] != signature:
                fail("相同 case_id 的用例配置冲突", location=location)
            can_merge = combined or (params.get("duplicate_case_policy", "error") == "merge_identical" and explicit_id is None and not previous["explicit_id"])
            if not can_merge:
                fail("清单配置重复；可显式 merge_identical 合并", "DUPLICATE_CASE_CONFIG", location)
            if can_merge:
                previous["locations"].append(location)
                previous["row_indexes"].append(index)
                merged_count += 1
                continue
        identifier_value = explicit_id or f"CV-{index + 1:06}"
        if identifier_value in identifiers:
            fail("用例编号重复或与自动编号冲突", location=location)
        identifiers.add(identifier_value)
        case.update(case_id=identifier_value, locations=[location], row_indexes=[index],
                    signature=signature, explicit_id=explicit_id)
        cases.append(case)
        groups[group_key] = case
    if not cases or len(cases) > 1000:
        fail("有效用例须为1～1000条", "INPUT_LIMIT_EXCEEDED")
    return cases, dataset, {"input_rows": len(rows), "enabled_rows": len(rows) - skipped,
                            "merged_rows": merged_count, "skipped_rows": skipped}


class Dialect:
    MINIMUM = {"sqlite": (3, 35), "postgresql": (12,), "mysql": (8,), "mariadb": (10, 2),
               "sqlserver": (14,), "oracle": (12, 2), "trino": (483,)}

    def __init__(self, engine, version):
        if engine not in self.MINIMUM:
            fail("该 SQL 入口尚未实现本插件所需的关联/标量子查询/结果投影组合；请选已支持引擎", "UNSUPPORTED_ENGINE")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", version):
            fail("engine_version 须为显式数字版本")
        version_parts = tuple(int(item) for item in version.split("."))
        if version_parts < self.MINIMUM[engine]:
            fail("引擎版本低于该方言的支持下限", "UNSUPPORTED_ENGINE")
        self.engine = engine

    def identifier(self, value):
        value = identifier(value)
        if self.engine in {"postgresql", "oracle"}:
            limit = 63 if self.engine == "postgresql" else 128
            length, unit = len(value.encode("utf-8")), "UTF-8字节"
        elif self.engine == "sqlserver":
            limit = 128
            length, unit = len(value.encode("utf-16-le")) // 2, "UTF-16码元"
        else:
            limit = 64 if self.engine in {"mysql", "mariadb"} else 128
            length, unit = len(value), "字符"
        if length > limit:
            fail(f"{self.engine} 标识符超过{limit}{unit}：{value}")
        return value

    def quote(self, value):
        value = self.identifier(value)
        if self.engine in {"mysql", "mariadb"}:
            return "`" + value + "`"
        if self.engine == "sqlserver":
            return "[" + value + "]"
        return '"' + value + '"'

    def table(self, value):
        segments = parts(value)
        if self.engine in {"sqlite", "mysql", "mariadb", "oracle"} and len(segments) > 2:
            fail("当前方言对象名最多为两段", "UNSUPPORTED_COMBINATION")
        return ".".join(self.quote(item) for item in segments)

    def column(self, alias, value):
        return alias + "." + self.quote(value)

    def literal(self, value, kind="text"):
        if value is None:
            return "NULL"
        if kind == "numeric":
            return value_type(value, "numeric", {})
        if not isinstance(value, str) or "\x00" in value:
            fail("文本常量须为无 NUL 的字符串")
        if self.engine == "oracle" and len(value.encode("utf-8")) > 4000:
            fail("Oracle 文本常量超过4000字节", "INPUT_LIMIT_EXCEEDED")
        if self.engine in {"mysql", "mariadb"}:
            return "CONVERT(0x" + value.encode("utf-8").hex() + " USING utf8mb4)" if value else "''"
        prefix = "N" if self.engine == "sqlserver" else ""
        if self.engine == "postgresql":
            prefix = "E"
            value = value.replace("\\", "\\\\")
        return prefix + "'" + value.replace("'", "''") + "'"

    def inline(self, value, kind):
        if kind == "numeric":
            return "CAST(" + self.literal(value, kind) + " AS " + ("NUMERIC" if self.engine == "sqlite" else "DECIMAL(38,18)") + ")"
        return self.text(self.literal(value, kind))

    def comparable(self, expression, kind):
        if kind == "numeric":
            return expression
        if self.engine == "sqlite":
            return expression + " COLLATE BINARY"
        if self.engine == "postgresql":
            return expression + ' COLLATE "C"'
        if self.engine in {"mysql", "mariadb"}:
            return "CAST(" + expression + " AS BINARY)"
        if self.engine == "sqlserver":
            return "CAST(" + expression + " AS NVARCHAR(MAX)) COLLATE Latin1_General_100_BIN2"
        if self.engine == "trino":
            return expression
        if self.engine == "oracle":
            return "CAST(" + expression + " AS VARCHAR2(4000 CHAR)) COLLATE BINARY"
        return expression + " COLLATE BINARY"

    def equal(self, left, right, kind):
        equality = self.comparable(left, kind) + " = " + self.comparable(right, kind)
        if self.engine == "sqlserver" and kind == "text":
            equality = "(" + equality + " AND DATALENGTH(" + self.comparable(left, kind) + ") = DATALENGTH(" + self.comparable(right, kind) + "))"
        return equality

    def text(self, expression):
        kind = {"sqlite": "TEXT", "postgresql": "TEXT", "mysql": "CHAR CHARACTER SET utf8mb4", "mariadb": "CHAR CHARACTER SET utf8mb4",
                "sqlserver": "NVARCHAR(MAX)", "oracle": "VARCHAR2(4000 CHAR)", "trino": "VARCHAR"}[self.engine]
        return "CAST(" + expression + " AS " + kind + ")"

    def text_type(self):
        return {"sqlite": "TEXT", "postgresql": "TEXT", "mysql": "LONGTEXT CHARACTER SET utf8mb4",
                "mariadb": "LONGTEXT CHARACTER SET utf8mb4", "sqlserver": "NVARCHAR(MAX)",
                "oracle": "VARCHAR2(4000 CHAR)", "trino": "VARCHAR"}[self.engine]

    def typed(self, expression, kind):
        if kind == "count":
            integer = "NUMBER(19)" if self.engine == "oracle" else "INTEGER" if self.engine == "sqlite" else "SIGNED" if self.engine in {"mysql", "mariadb"} else "BIGINT"
            return "CAST(" + expression + " AS " + integer + ")"
        return self.text(expression)

    def count(self):
        return "COUNT_BIG(*)" if self.engine == "sqlserver" else "COUNT(*)"

    def with_query(self, definitions, query):
        return "WITH " + ",\n".join(definitions) + "\n" + query

    def insert(self, table, columns, definitions, query):
        opening = "INSERT INTO " + table + " (" + ", ".join(self.quote(name) for name in columns) + ")\n"
        if self.engine in {"mysql", "mariadb", "oracle"}:
            return opening + self.with_query(definitions, query)
        return self.with_query(definitions, opening + query)


def filter_sql(items, alias, dialect):
    expressions = []
    for item in items:
        field = dialect.column(alias, item["field"])
        operator = item["operator"]
        if operator in {"is_null", "is_not_null"}:
            expressions.append(field + (" IS NULL" if operator == "is_null" else " IS NOT NULL"))
        elif operator == "in":
            expressions.append("(" + " OR ".join(dialect.equal(field, dialect.literal(value, item["value_type"]), item["value_type"])
                                                  for value in item["value"]) + ")")
        else:
            expression = dialect.equal(field, dialect.literal(item["value"], item["value_type"]), item["value_type"])
            expressions.append("NOT (" + expression + ")" if operator == "ne" else expression)
    return " AND ".join(expressions) or "1=1"


def load_mapping(params, dataset, cases):
    if params["mapping_mode"] == "database":
        code_table = object_keys(params.get("code_table"), {"table_parts", "columns"}, "code_table 须配置对象和列")
        table = parts(code_table.get("table_parts"))
        columns = column_mapping(code_table.get("columns"))
        if not set(MAPPING_COLUMNS) <= set(columns):
            fail("数据库码值表缺少核心字段映射")
        return table, columns, None, []
    if params["mapping_source"] == "same_workbook":
        path = params["test_plan_input"]
        if dataset["format"] not in {"xlsx", "xlsm"}:
            fail("same_workbook 必须为 Excel")
        if params.get("file_layout", "separate_relations") == "combined_rows":
            if params.get("mapping_options") and params["mapping_options"] != params.get("test_plan_options", {}):
                fail("合并明细须使用同一读取区域")
            mapped_dataset = dataset
        else:
            if same_region(params.get("test_plan_options", {}), params.get("mapping_options", {})):
                fail("两个逻辑关系读取区域相交，请选择 combined_rows 或明确分区")
            mapped_dataset = read_input(path, params.get("mapping_options", {}))
    else:
        path = params["mapping_input"]
        if Path(path).resolve() == Path(params["test_plan_input"]).resolve():
            fail("同一文件请使用 same_workbook")
        mapped_dataset = read_input(path, params.get("mapping_options", {}))
    columns = column_mapping(params.get("mapping_columns", MAPPING_COLUMNS))
    if not set(MAPPING_COLUMNS) <= set(columns):
        fail("文件码值表缺少核心字段映射")
    rows = project(mapped_dataset, columns)
    if not rows:
        fail("码值映射文件为空")
    for row, location in zip(rows, mapped_dataset["locations"]):
        for field, value in row.items():
            if field in {"code_label", "source_label"} and value == "":
                row[field] = value = None
            nullable = field not in {"domain", "source_code", "target_code"} or (field == "target_code" and params.get("allow_mapping_null", False))
            if field == "target_code" and value is None and nullable and mapped_dataset["format"] != "json":
                fail("Excel/CSV 的 NULL 映射须使用显式 null_token", location=location)
            value_type(value, "text" if isinstance(value, str) else "numeric", params, nullable=nullable, location=location)
    return None, columns, (rows, mapped_dataset["locations"]), mapped_dataset["warnings"]


def mapping_relation(case, mapping, params, dialect, prefix):
    table, columns, data, warnings = mapping
    if data is None:
        return [], dialect.table(table), columns
    rows, locations = data
    indexes = case["row_indexes"] if params.get("file_layout", "separate_relations") == "combined_rows" else range(len(rows))
    fields = ["domain", "source_code", "target_code", "code_label"]
    if "source_label" in columns:
        fields.append("source_label")
    fields += [name for name in columns if name not in fields]
    selections, signatures = [], set()
    rendered_bytes = 0
    for index in indexes:
        row, location = rows[index], locations[index]
        domain = value_type(row["domain"], case["types"]["domain"], params, location=location)
        if case["types"]["domain"] == "text" and domain != case["domain_value"]:
            continue
        values = {}
        for field in fields:
            kind = case["types"].get(field, "text")
            if field == "target_code":
                kind = case["types"]["target_code"]
            elif field == "source_code":
                kind = case["types"]["source_code"]
            elif field == "domain":
                kind = case["types"]["domain"]
            elif field not in {"code_label", "source_label"}:
                kind = case["types"]["dimensions"].get(field, "text")
            nullable = field not in {"domain", "source_code", "target_code"} or (field == "target_code" and params.get("allow_mapping_null", False))
            if (field == "target_code" and row[field] is None and nullable
                    and params["mapping_mode"] == "file" and not params.get("null_token")
                    and Path(params.get("mapping_input", params["test_plan_input"])).suffix.lower() != ".json"):
                fail("Excel/CSV 的 NULL 映射须使用显式 null_token", location=location)
            raw = row[field]
            if field in {"code_label", "source_label"} and raw == "":
                raw = None
            values[field] = value_type(raw, kind, params, nullable=nullable, location=location)
        domain_matches = (Decimal(values["domain"]) == Decimal(case["domain_value"])) if case["types"]["domain"] == "numeric" else values["domain"] == case["domain_value"]
        if domain_matches:
            eligible, dynamic_fields = True, []
            for item in case["mapping_conditions"]:
                field = item["mapping_field"]
                if field not in values:
                    fail("映射字段未配置：" + field)
                if item.get("value_source") == "source_field":
                    dynamic_fields.append(field)
                    if values[field] is None:
                        eligible = False
                else:
                    eligible = eligible and local_condition(item, values[field], case)
            if eligible:
                signature = json.dumps([values["domain"], values["source_code"]] + [values[field] for field in dynamic_fields], ensure_ascii=False)
                if signature in signatures:
                    fail("当前用例完整有效映射键重复", "DUPLICATE_MAPPING", location)
                signatures.add(signature)
        expressions = []
        for field in fields:
            kind = case["types"]["target_code"] if field == "target_code" else case["types"].get(field, "text")
            if field not in {"domain", "source_code", "target_code", "code_label", "source_label"}:
                kind = case["types"]["dimensions"].get(field, "text")
            expression = dialect.inline(values[field], kind)
            expressions.append(expression + " AS " + dialect.quote(field))
        selection = "SELECT " + ", ".join(expressions)
        if dialect.engine == "oracle":
            selection += " FROM DUAL"
        rendered_bytes += len(selection.encode("utf-8"))
        if rendered_bytes > params.get("max_sql_bytes", 50 * 1024 * 1024):
            fail("内联映射超出SQL大小预算", "OUTPUT_LIMIT_EXCEEDED")
        selections.append(selection)
        if len(selections) > (400 if dialect.engine == "sqlite" else 1000):
            fail("单用例内联码值超过当前方言上限", "INPUT_LIMIT_EXCEEDED")
    if not selections:
        selections = ["SELECT " + ", ".join(dialect.inline(None, case["types"].get(field, case["types"]["dimensions"].get(field, "text"))) + " AS " + dialect.quote(field) for field in fields)
                      + (" FROM DUAL" if dialect.engine == "oracle" else "") + " WHERE 1=0"]
    name = prefix + "mapping"
    return [name + " AS (\n" + "\nUNION ALL\n".join(selections) + "\n)"], name, {field: field for field in fields}


def local_condition(item, value, case):
    operator = item["operator"]
    if operator == "is_null":
        return value is None
    if operator == "is_not_null":
        return value is not None
    if value is None:
        return False
    if item.get("value_source") == "case_field":
        right = case[item["field"]]
        right = ".".join(right) if isinstance(right, list) else right
        kind = case["types"]["dimensions"].get(item["mapping_field"], "text")
    else:
        right, kind = item["value"], item["value_type"]
    if kind == "numeric":
        value = Decimal(value_type(value, "numeric", {}))
        right = [Decimal(value_type(entry, "numeric", {})) for entry in right] if isinstance(right, list) else Decimal(value_type(right, "numeric", {}))
    if operator == "in":
        return value in right
    return value != right if operator == "ne" else value == right


def validate_configuration(params):
    object_keys(params, CONFIG_KEYS, "含未知参数")
    for key in {"test_plan_input", "sql_engine", "engine_version", "mapping_mode"}:
        if not isinstance(params.get(key), str) or not params[key]:
            fail("缺少必填参数：" + key)
    enums = {
        "mapping_mode": ({"database", "file"}, None),
        "output_mode": ({"select_only", "insert_only", "select_and_insert"}, "select_only"),
        "statement_layout": ({"separate", "union_all"}, "separate"),
        "file_layout": ({"separate_relations", "combined_rows"}, "separate_relations"),
        "duplicate_case_policy": ({"error", "merge_identical"}, "error"),
        "numeric_text_policy": ({"reject", "convert"}, "reject"),
    }
    for name, (values, default) in enums.items():
        if params.get(name, default) not in values:
            fail("枚举配置不支持：" + name)
    for name in {"include_summary", "include_reverse_check", "allow_mapping_null"}:
        if name in params and type(params[name]) is not bool:
            fail("配置须为布尔值：" + name)
    mode = params.get("output_mode", "select_only")
    if mode == "select_only" and set(params) & {"store_scope", "result_table", "create_policy", "result_columns"}:
        fail("仅查询模式不接受结果表/保存配置")
    if mode == "insert_only" and "result_scope" in params:
        fail("仅落表使用 store_scope，不接受查询范围")
    for name in {"store_scope", "result_scope"}:
        if params.get(name, "all") not in {"all", "errors_only"}:
            fail("范围仅支持 all/errors_only")
    if mode == "select_and_insert" and params.get("store_scope", "all") == "errors_only":
        if params.get("result_scope", "all") == "all" or params.get("include_summary"):
            fail("仅保存异常不能查询/汇总全量")
    if params.get("create_policy", "create_new") not in {"create_new", "create_if_missing"}:
        fail("建表策略不支持")
    batch_size = params.get("union_batch_size", 50)
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        fail("union_batch_size 须为1～100")
    if params.get("statement_layout", "separate") != "union_all" and "union_batch_size" in params:
        fail("独立模式不接受组合数量")
    if params["mapping_mode"] == "database":
        if set(params) & {"mapping_input", "mapping_source", "mapping_options", "mapping_columns"} or params.get("file_layout") == "combined_rows":
            fail("数据库映射模式不接受文件映射配置")
    else:
        if "code_table" in params or params.get("mapping_source") not in {"same_workbook", "separate_file"}:
            fail("文件模式须明确来源且不接受数据库映射对象")
        if params["mapping_source"] == "same_workbook" and "mapping_input" in params:
            fail("同工作簿不需重复输入 mapping_input")
        if params["mapping_source"] == "separate_file" and not params.get("mapping_input"):
            fail("独立文件模式缺少 mapping_input")
        if params["mapping_source"] == "separate_file" and params.get("file_layout") == "combined_rows":
            fail("合并明细必须在同一工作簿")
    if params.get("null_token") is not None and (not isinstance(params["null_token"], str) or not params["null_token"]):
        fail("null_token 须为非空文本")
    dialect = Dialect(params["sql_engine"], params["engine_version"])
    if dialect.engine == "trino" and mode != "select_only":
        fail("Trino 首期仅查询；连接器写能力尚未验收", "UNSUPPORTED_COMBINATION")
    return dialect


STATUSES = (
    "SAME", "DIFFERENT", "INVALID_TARGET_KEY", "DUPLICATE_TARGET_KEY", "SOURCE_MISSING",
    "DUPLICATE_SOURCE_MATCH", "SOURCE_CODE_NULL", "MAPPING_MISSING", "DUPLICATE_MAPPING",
    "NULL_VALUE", "TARGET_MISSING", "INVALID_SOURCE_KEY", "DUPLICATE_SOURCE_KEY",
)
RESULT_FIELDS = (
    "run_id", "case_id", "check_direction", "source_table", "source_field", "source_code",
    "target_table", "target_field", "actual_target_code", "code_domain", "expected_target_code",
    "source_label", "code_label", "comparison", "check_status", "source_match_count",
    "target_match_count", "mapping_match_count",
)


def key_predicate(case, dialect, left_alias, left_fields, right_alias, right_fields):
    return " AND ".join(dialect.equal(dialect.column(left_alias, left), dialect.column(right_alias, right), kind)
                        for left, right, kind in zip(left_fields, right_fields, case["types"]["keys"]))


def invalid_keys(case, dialect, alias, fields):
    predicates = []
    for field, kind in zip(fields, case["types"]["keys"]):
        expression = dialect.column(alias, field)
        predicates.append(expression + " IS NULL")
        if kind == "text":
            predicates.append(dialect.equal(expression, dialect.literal(""), kind))
    return "(" + " OR ".join(predicates) + ")"


def candidate(relation, alias, predicate, expression):
    return "(SELECT " + expression + " FROM " + relation + " " + alias + " WHERE " + predicate + ")"


def dimensions_for(case):
    dimensions = []
    for item in case["mapping_conditions"]:
        if item.get("value_source") == "source_field":
            entry = (item["mapping_field"], item["field"], case["types"]["dimensions"].get(item["mapping_field"], "text"))
            if entry not in dimensions:
                dimensions.append(entry)
    return dimensions


def mapping_predicate(case, dialect, columns, dimensions):
    predicates = [
        dialect.equal(dialect.column("m", columns["domain"]), dialect.literal(case["domain_value"], case["types"]["domain"]), case["types"]["domain"]),
        dialect.equal(dialect.column("m", columns["source_code"]), "q.source_code", case["types"]["source_code"]),
    ]
    for item in case["mapping_conditions"]:
        name = item["mapping_field"]
        if name not in columns:
            fail("映射字段未配置：" + name)
        operator = item["operator"]
        field = dialect.column("m", columns[name])
        if operator in {"is_null", "is_not_null"}:
            predicates.append(field + (" IS NULL" if operator == "is_null" else " IS NOT NULL"))
            continue
        source = item["value_source"]
        if source == "literal":
            predicates.append(filter_sql([{**item, "field": columns[name]}], "m", dialect))
        else:
            kind = case["types"]["dimensions"].get(name, "text")
            if source == "source_field":
                index = dimensions.index((name, item["field"], kind))
                right = "q.dim" + str(index + 1)
            else:
                value = case[item["field"]]
                value = ".".join(value) if isinstance(value, list) else value
                right = dialect.literal(value, kind)
            predicates.append(dialect.equal(field, right, kind))
    return " AND ".join(predicates)


def compile_case(case, mapping, params, dialect, prefix, shape):
    definitions, relation, columns = mapping_relation(case, mapping, params, dialect, prefix)
    source, target = dialect.table(case["source_table"]), dialect.table(case["target_table"])
    keys = ["key" + str(index + 1) for index in range(len(case["source_keys"]))]
    source_where = key_predicate(case, dialect, "s", case["source_keys"], "q", keys) + " AND " + filter_sql(case["source_filters"], "s", dialect)
    target_where = key_predicate(case, dialect, "t", case["target_keys"], "q", keys) + " AND " + filter_sql(case["target_filters"], "t", dialect)
    target_self = key_predicate(case, dialect, "t", case["target_keys"], "base", case["target_keys"]) + " AND " + filter_sql(case["target_filters"], "t", dialect)
    source_base = key_predicate(case, dialect, "s", case["source_keys"], "base", case["target_keys"]) + " AND " + filter_sql(case["source_filters"], "s", dialect)
    base_fields = [dialect.column("base", field) + " AS " + dialect.quote(key) for field, key in zip(case["target_keys"], keys)]
    base_fields += [dialect.column("base", case["target_code_field"]) + " AS actual_code",
                    candidate(source, "s", source_base, dialect.count()) + " AS source_match_count",
                    candidate(target, "t", target_self, dialect.count()) + " AS target_match_count"]
    definitions.append(prefix + "base AS (SELECT " + ",\n".join(base_fields) + " FROM " + target + " base WHERE " + filter_sql(case["target_filters"], "base", dialect) + ")")
    dimensions = dimensions_for(case)
    extracted = [("source_code", case["source_code_field"])]
    extracted += [("source_key" + str(index + 1), field) for index, field in enumerate(case["source_keys"])]
    extracted += [("dim" + str(index + 1), entry[1]) for index, entry in enumerate(dimensions)]
    expressions = ["CASE WHEN q.source_match_count = 1 THEN " + candidate(source, "s", source_where, dialect.column("s", field)) + " END AS " + name
                   for name, field in extracted]
    definitions.append(prefix + "source AS (SELECT q.*, " + ",\n".join(expressions) + " FROM " + prefix + "base q)")
    mapped_where = mapping_predicate(case, dialect, columns, dimensions)
    definitions.append(prefix + "counted AS (SELECT q.*, CASE WHEN q.source_match_count = 1 AND q.source_code IS NOT NULL THEN "
                       + candidate(relation, "m", mapped_where, dialect.count()) + " END AS mapping_match_count FROM " + prefix + "source q)")
    retrieved = []
    for name, logical in (("expected_code", "target_code"), ("code_label", "code_label"), ("source_label", "source_label")):
        expression = candidate(relation, "m", mapped_where, dialect.column("m", columns[logical])) if logical in columns else "NULL"
        retrieved.append("CASE WHEN q.mapping_match_count = 1 THEN " + expression + " END AS " + name)
    definitions.append(prefix + "mapped AS (SELECT q.*, " + ",\n".join(retrieved) + " FROM " + prefix + "counted q)")
    null_status = "SAME" if case["nulls_equal"] else "NULL_VALUE"
    status = ("CASE WHEN " + invalid_keys(case, dialect, "q", keys) + " THEN 'INVALID_TARGET_KEY'"
              " WHEN q.target_match_count > 1 THEN 'DUPLICATE_TARGET_KEY'"
              " WHEN q.source_match_count = 0 THEN 'SOURCE_MISSING'"
              " WHEN q.source_match_count > 1 THEN 'DUPLICATE_SOURCE_MATCH'"
              " WHEN q.source_code IS NULL THEN 'SOURCE_CODE_NULL'"
              " WHEN q.mapping_match_count = 0 THEN 'MAPPING_MISSING'"
              " WHEN q.mapping_match_count > 1 THEN 'DUPLICATE_MAPPING'"
              " WHEN q.actual_code IS NULL AND q.expected_code IS NULL THEN '" + null_status + "'"
              " WHEN q.actual_code IS NULL OR q.expected_code IS NULL THEN 'DIFFERENT'"
              " WHEN " + dialect.equal("q.actual_code", "q.expected_code", case["types"]["target_code"]) + " THEN 'SAME' ELSE 'DIFFERENT' END")
    definitions.append(prefix + "checked AS (SELECT q.*, " + status + " AS check_status FROM " + prefix + "mapped q)")
    expressions = result_projection(case, params, dialect, shape, keys, dimensions, reverse=False)
    definitions.append(prefix + "forward AS (SELECT " + expressions + " FROM " + prefix + "checked q)")
    outputs = [prefix + "forward"]
    if params.get("include_reverse_check"):
        reverse_fields = [dialect.column("base", field) + " AS " + dialect.quote(key) for field, key in zip(case["source_keys"], keys)]
        reverse_source = key_predicate(case, dialect, "s", case["source_keys"], "base", case["source_keys"]) + " AND " + filter_sql(case["source_filters"], "s", dialect)
        reverse_target = key_predicate(case, dialect, "t", case["target_keys"], "base", case["source_keys"]) + " AND " + filter_sql(case["target_filters"], "t", dialect)
        reverse_fields += [dialect.column("base", case["source_code_field"]) + " AS source_code",
                           candidate(source, "s", reverse_source, dialect.count()) + " AS source_match_count",
                           candidate(target, "t", reverse_target, dialect.count()) + " AS target_match_count"]
        definitions.append(prefix + "reverse_base AS (SELECT " + ",\n".join(reverse_fields) + " FROM " + source + " base WHERE " + filter_sql(case["source_filters"], "base", dialect) + ")")
        reverse_status = "CASE WHEN " + invalid_keys(case, dialect, "q", keys) + " THEN 'INVALID_SOURCE_KEY' WHEN q.source_match_count > 1 THEN 'DUPLICATE_SOURCE_KEY' ELSE 'TARGET_MISSING' END"
        definitions.append(prefix + "reverse_checked AS (SELECT q.*, " + reverse_status + " AS check_status FROM " + prefix + "reverse_base q WHERE "
                           + invalid_keys(case, dialect, "q", keys) + " OR q.source_match_count > 1 OR q.target_match_count = 0)")
        definitions.append(prefix + "reverse AS (SELECT " + result_projection(case, params, dialect, shape, keys, [], reverse=True) + " FROM " + prefix + "reverse_checked q)")
        outputs.append(prefix + "reverse")
    return definitions, outputs


def result_shape(cases):
    key_count = max(len(case["source_keys"]) for case in cases)
    dimension_count = max(len(dimensions_for(case)) for case in cases)
    fields = list(RESULT_FIELDS)
    for index in range(1, key_count + 1):
        fields += ["source_key_field_" + str(index), "source_key_" + str(index), "target_key_field_" + str(index), "target_key_" + str(index)]
    for index in range(1, dimension_count + 1):
        fields += ["dimension_mapping_field_" + str(index), "dimension_source_field_" + str(index), "dimension_value_" + str(index)]
    return fields


def result_projection(case, params, dialect, shape, keys, dimensions, reverse):
    expressions = {
        "run_id": dialect.literal(params["run_id"]), "case_id": dialect.literal(case["case_id"]),
        "check_direction": dialect.literal("source_to_target" if reverse else "target_to_source"),
        "source_table": dialect.literal(".".join(case["source_table"])), "source_field": dialect.literal(case["source_code_field"]),
        "target_table": dialect.literal(".".join(case["target_table"])), "target_field": dialect.literal(case["target_code_field"]),
        "code_domain": dialect.literal(case["domain_value"], case["types"]["domain"]),
        "source_code": "q.source_code", "actual_target_code": "NULL" if reverse else "q.actual_code",
        "expected_target_code": "NULL" if reverse else "q.expected_code", "source_label": "NULL" if reverse else "q.source_label",
        "code_label": "NULL" if reverse else "q.code_label", "check_status": "q.check_status",
        "comparison": "CASE q.check_status WHEN 'SAME' THEN " + dialect.literal("相同") + " WHEN 'DIFFERENT' THEN " + dialect.literal("不同") + " ELSE " + dialect.literal("无法比较") + " END",
        "source_match_count": "q.source_match_count", "target_match_count": "q.target_match_count",
        "mapping_match_count": "NULL" if reverse else "q.mapping_match_count",
    }
    for index, key in enumerate(keys, 1):
        expressions["source_key_field_" + str(index)] = dialect.literal(case["source_keys"][index - 1])
        expressions["target_key_field_" + str(index)] = dialect.literal(case["target_keys"][index - 1])
        expressions["source_key_" + str(index)] = dialect.column("q", key) if reverse else "q.source_key" + str(index)
        expressions["target_key_" + str(index)] = "NULL" if reverse else dialect.column("q", key)
    for index, entry in enumerate(dimensions, 1):
        expressions["dimension_mapping_field_" + str(index)] = dialect.literal(entry[0])
        expressions["dimension_source_field_" + str(index)] = dialect.literal(entry[1])
        expressions["dimension_value_" + str(index)] = "q.dim" + str(index)
    return ",\n".join(dialect.typed(expressions.get(field, "NULL"), "count" if field.endswith("_count") else "text") + " AS " + dialect.quote(field) for field in shape)


def internal_namespace(cases, mapping, output_table):
    objects = [case[field] for case in cases for field in ("source_table", "target_table")]
    objects.append(output_table)
    if mapping[0] is not None:
        objects.append(mapping[0])
    reserved = {name[-1].casefold() for name in objects}
    attempt = 0
    prefix = "cvcheck_"
    while any(name.startswith(prefix) for name in reserved):
        attempt += 1
        prefix = "cvcheck" + str(attempt) + "_"
    return prefix


def summary_query(cases, params, dialect, relation, namespace):
    case_id = dialect.quote("case_id")
    direction_field = dialect.quote("check_direction")
    status_field = dialect.quote("check_status")
    total = dialect.quote("total_rows")
    same = dialect.column("t", "same_rows")
    different = dialect.column("t", "different_rows")
    total_reference = dialect.column("t", "total_rows")
    summary_cases = namespace + "summary_cases"
    summary_counts = namespace + "summary_counts"
    rows = []
    for case in cases:
        for direction in (["target_to_source", "source_to_target"] if params.get("include_reverse_check") else ["target_to_source"]):
            rows.append("SELECT " + dialect.literal(case["case_id"]) + " AS " + case_id + ", " + dialect.literal(direction) + " AS " + direction_field + (" FROM DUAL" if dialect.engine == "oracle" else ""))
    sums = [dialect.count() + " AS " + total]
    sums += ["SUM(CASE WHEN " + status_field + " = '" + status + "' THEN 1 ELSE 0 END) AS " + dialect.quote(status.lower() + "_rows") for status in STATUSES]
    definitions = [summary_cases + " AS (" + " UNION ALL ".join(rows) + ")",
                   summary_counts + " AS (SELECT " + dialect.comparable(case_id, "text") + " AS " + case_id + ", "
                   + dialect.comparable(direction_field, "text") + " AS " + direction_field + ", " + ", ".join(sums)
                   + " FROM " + relation + " GROUP BY " + dialect.comparable(case_id, "text") + ", " + dialect.comparable(direction_field, "text") + ")"]
    selections = [dialect.column("c", "case_id"), dialect.column("c", "check_direction"), "COALESCE(" + total_reference + ", 0) AS " + total]
    selections += ["COALESCE(" + dialect.column("t", status.lower() + "_rows") + ", 0) AS " + dialect.quote(status.lower() + "_rows") for status in STATUSES]
    selections += ["COALESCE(" + total_reference + " - " + same + " - " + different + ", 0) AS " + dialect.quote("not_comparable_rows"),
                   "CASE WHEN " + total_reference + " IS NULL OR " + total_reference + " = 0 THEN 'NO_DATA' ELSE 'HAS_DATA' END AS " + dialect.quote("data_status"),
                   "CASE WHEN " + dialect.column("c", "check_direction") + " = 'target_to_source' AND " + total_reference + " > 0 THEN 1.0 * " + same + " / " + total_reference + " END AS " + dialect.quote("pass_rate")]
    return definitions, "SELECT " + ",\n".join(selections) + " FROM " + summary_cases + " c LEFT JOIN " + summary_counts + " t ON " + dialect.equal(dialect.column("c", "case_id"), dialect.column("t", "case_id"), "text") + " AND " + dialect.equal(dialect.column("c", "check_direction"), dialect.column("t", "check_direction"), "text")


def generate(cases, mapping, params, dialect):
    maximum = params.get("max_sql_bytes", 50 * 1024 * 1024)
    if type(maximum) is not int or not 1024 <= maximum <= 50 * 1024 * 1024:
        fail("max_sql_bytes 须为1024～52428800")
    shape = result_shape(cases)
    mode = params.get("output_mode", "select_only")
    physical = params.get("result_columns", {})
    object_keys(physical, set(shape), "结果字段映射含未知字段")
    physical = {field: dialect.identifier(physical.get(field, field)) for field in shape}
    if len({name.casefold() for name in physical.values()}) != len(shape):
        fail("结果表物理列不能重复（含大小写变体）")
    output_table = parts(params.get("result_table", "codecheck_" + hashlib.sha256(params["run_id"].encode()).hexdigest()[:16]))
    if mode != "select_only":
        inputs = [case[field] for case in cases for field in ("source_table", "target_table")]
        if mapping[0] is not None:
            inputs.append(mapping[0])
        if any(item[-1].casefold() == output_table[-1].casefold() for item in inputs):
            fail("结果表不能为业务输入对象")
    result_table = dialect.table(output_table)
    for field in mapping[1].values() if mapping[0] is not None else mapping[1]:
        dialect.identifier(field)
    namespace = internal_namespace(cases, mapping, output_table)
    dialect.identifier(namespace + "cv" + str(len(cases)) + "_reverse_checked")
    all_results = namespace + "all_results"
    saved_results = namespace + "saved_results"
    errors = " WHERE " + dialect.quote("check_status") + " <> 'SAME'"
    statements = []
    rendered_bytes = 0

    def append(statement):
        nonlocal rendered_bytes
        rendered_bytes += len(statement.encode("utf-8")) + 3
        if rendered_bytes > maximum:
            fail("生成脚本超过大小限制；请拆分清单或使用数据库码值表", "OUTPUT_LIMIT_EXCEEDED")
        statements.append(statement)
    if mode != "select_only":
        if params.get("create_policy", "create_new") == "create_if_missing" and dialect.engine not in {"sqlite", "mysql", "mariadb", "postgresql"}:
            fail("该方言不支持当前 create_if_missing 策略", "UNSUPPORTED_COMBINATION")
        ddl = "CREATE TABLE " + ("IF NOT EXISTS " if params.get("create_policy") == "create_if_missing" else "") + result_table
        columns = []
        for field in shape:
            kind = ("NUMBER(19)" if dialect.engine == "oracle" else "BIGINT") if field.endswith("_count") else dialect.text_type()
            columns.append(dialect.quote(physical[field]) + " " + kind)
        append(ddl + " (\n" + ",\n".join(columns) + "\n)")
    batch = params.get("union_batch_size", 50) if params.get("statement_layout", "separate") == "union_all" else 1
    for offset in range(0, len(cases), batch):
        group = cases[offset:offset + batch]
        definitions, relations = [], []
        definition_bytes = 0
        for index, case in enumerate(group, offset + 1):
            compiled, outputs = compile_case(case, mapping, params, dialect, namespace + "cv" + str(index) + "_", shape)
            definition_bytes += sum(len(definition.encode("utf-8")) for definition in compiled)
            if definition_bytes > maximum:
                fail("组合SQL超出大小预算", "OUTPUT_LIMIT_EXCEEDED")
            definitions.extend(compiled)
            relations.extend(outputs)
        definitions.append(all_results + " AS (" + "\nUNION ALL\n".join("SELECT * FROM " + name for name in relations) + ")")
        all_query = "SELECT " + ", ".join(dialect.quote(field) for field in shape) + " FROM " + all_results
        if mode == "select_only":
            append(dialect.with_query(definitions, all_query + (errors if params.get("result_scope") == "errors_only" else "")))
        else:
            append(dialect.insert(result_table, list(physical.values()), definitions, all_query + (errors if params.get("store_scope") == "errors_only" else "")))
        if params.get("include_summary") and (mode == "select_only" or (mode == "insert_only" and params.get("store_scope") == "errors_only")):
            extra, query = summary_query(group, params, dialect, all_results, namespace)
            append(dialect.with_query(definitions + extra, query))
    saved = "SELECT " + ", ".join(dialect.quote(physical[field]) + " AS " + dialect.quote(field) for field in shape) + " FROM " + result_table + " WHERE " + dialect.equal(dialect.quote(physical["run_id"]), dialect.literal(params["run_id"]), "text")
    if mode == "select_and_insert":
        append("SELECT * FROM (" + saved + ") " + saved_results + (errors if params.get("result_scope") == "errors_only" else ""))
    if params.get("include_summary") and mode != "select_only" and params.get("store_scope", "all") == "all":
        for offset in range(0, len(cases), batch):
            extra, query = summary_query(cases[offset:offset + batch], params, dialect, saved_results, namespace)
            append(dialect.with_query([saved_results + " AS (" + saved + ")"] + extra, query))
    script = ";\n\n".join(statements) + ";\n"
    if len(script.encode("utf-8")) > maximum:
        fail("生成脚本超过大小限制；请拆分清单或使用数据库码值表", "OUTPUT_LIMIT_EXCEEDED")
    return script, {"columns": physical, "result_table": output_table if mode != "select_only" else None,
                    "statement_count": len(statements), "sql_bytes": len(script.encode("utf-8"))}


class Plugin:
    def init(self, context):
        self.context = context

    def execute(self, command, params):
        if command != "sql.codecheck":
            fail("不支持的命令")
        params = copy.deepcopy(params)
        dialect = validate_configuration(params)
        params.setdefault("run_id", self.context.task.id)
        if not isinstance(params["run_id"], str) or not params["run_id"] or len(params["run_id"]) > 128:
            fail("run_id 须为1～128字符")
        cases, dataset, counts = load_cases(params)
        mapping = load_mapping(params, dataset, cases)
        script, generated = generate(cases, mapping, params, dialect)
        warnings = list(dict.fromkeys(dataset["warnings"] + mapping[3]))
        warnings += ["仅生成脚本，未执行数据库校验。所有源、目标和映射对象必须在同一 SQL 执行入口可见。",
                     "文本使用显式二进制/区分大小写比较；声明类型必须与数据库实际列一致。",
                     "文件重复有效映射键生成前拒绝；数据库映射由SQL按全部有效条件计数诊断，不任意选择映射。"]
        if mapping[2] and any(row.get("code_label") is None for row in mapping[2][0]):
            warnings.append("部分码值中文为空，结果说明投影NULL，不影响码值转换判定。")
        if dialect.engine != "sqlite":
            warnings.append("该方言已实现文本渲染但未在实际数据库验收。")
        if params.get("include_summary") and params.get("output_mode") == "insert_only" and params.get("store_scope") == "errors_only":
            warnings.append("仅异常落表的全量汇总另读业务表，与保存明细可能不是同一快照。")
        if params.get("output_mode", "select_only") != "select_only":
            warnings.append("执行前核对结果表结构；相同批次脚本重跑会追加结果，不保证幂等。不包含自动事务。")
        plan = {"schema_version": 1, "command": command, "run_id": params["run_id"], "sql_engine": params["sql_engine"],
                "engine_version": params["engine_version"], "mapping_mode": params["mapping_mode"],
                "output_mode": params.get("output_mode", "select_only"), "statement_layout": params.get("statement_layout", "separate"),
                "execution_status": "NOT_EXECUTED", "cases": cases, "mapping_columns": mapping[1],
                "mapping_locations": mapping[2][1] if mapping[2] else [], "warnings": warnings, **counts, **generated}
        plan["options"] = {key: value for key, value in params.items() if key not in {"test_plan_input", "mapping_input"}}
        plan["mapping_row_count"] = len(mapping[2][0]) if mapping[2] else None
        plan["source_location_contract"] = "文件原始路径与快照hash见任务manifest；来源行/读取区域见cases、mapping_locations及options"
        plan["adapter_status"] = "locally-tested" if dialect.engine == "sqlite" else "implemented-unverified"
        files = [self.context.files.write_text("codecheck.sql", script),
                 self.context.files.write_text("codecheck-plan.json", json.dumps(plan, ensure_ascii=False, indent=2))]
        report = "# 码值转换校验脚本\n\n仅生成，未执行。\n\n" + f"用例数：{len(cases)}；SQL语句数：{generated['statement_count']}。\n\n" + "\n".join("- " + warning for warning in warnings)
        files.append(self.context.files.write_text("codecheck-report.md", report + "\n"))
        return Result("success", "码值校验 SQL 已生成，未执行数据库校验",
                      data={"case_count": len(cases), "execution_status": "NOT_EXECUTED", **counts, **generated},
                      files=files, warnings=warnings)

    def destroy(self):
        self.context = None
