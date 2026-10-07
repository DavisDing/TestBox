"""Declarative quality checks; no expressions, SQL execution or source writes."""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from testbox.sdk import PluginError, Result, normalize_dataset, read_dataset

RULE_KEYS = {
    "required": {"fields"}, "unique": {"fields"},
    "type": {"field", "value_type"}, "enum": {"field", "values"},
    "range": {"field", "min", "max"}, "length": {"field", "min", "max"},
    "compare": {"left", "right", "operator"}, "row_count": {"min", "max"},
}
VALUE_TYPES = {"string", "integer", "number", "boolean", "date", "datetime"}
OPERATORS = {"eq", "ne", "lt", "le", "gt", "ge"}
MISSING = object()


def invalid(index: int | None, reason: str) -> None:
    # Never interpolate supplied configuration or cell values into diagnostics.
    prefix = "参数" if index is None else f"规则 #{index + 1}"
    raise PluginError("INVALID_PARAMS", f"{prefix}配置无效：{reason}")


def numeric(value: Any) -> Decimal:
    """Exact finite decimal comparison, including explicitly supplied text bounds."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError()
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError() from None
    if not number.is_finite():
        raise ValueError()
    return number


def canonical(value: Any, depth: int = 0) -> tuple:
    """Hashable, type-aware JSON equality: True != 1, object order irrelevant."""
    if depth > 64:
        raise ValueError()
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float, Decimal)):
        return ("number", numeric(value))
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(canonical(item, depth + 1) for item in value))
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return ("object", tuple(sorted((key, canonical(item, depth + 1)) for key, item in value.items())))
    raise ValueError()


def validate_params(params: dict) -> list[dict]:
    if not isinstance(params, dict) or set(params) - {"input", "options", "normalize", "rules", "max_issues"}:
        invalid(None, "未知参数")
    if not isinstance(params.get("input"), str) or not params["input"]:
        invalid(None, "input 必须为非空文件路径")
    for key in ("options", "normalize"):
        if not isinstance(params.get(key, {}), dict):
            invalid(None, "读取和标准化配置必须是对象")
    maximum = params.get("max_issues", 1000)
    if type(maximum) is not int or not 0 <= maximum <= 10000:
        invalid(None, "max_issues 必须为 0..10000 的整数")
    rules = params.get("rules")
    if not isinstance(rules, list) or not 1 <= len(rules) <= 1000:
        invalid(None, "rules 必须含 1..1000 条规则")
    validated, identifiers = [], set()
    for index, original in enumerate(rules):
        if not isinstance(original, dict):
            invalid(index, "规则必须是对象")
        kind = original.get("type")
        if not isinstance(kind, str) or kind not in RULE_KEYS:
            invalid(index, "未知规则，regex/SQL/表达式不支持")
        if set(original) - (RULE_KEYS[kind] | {"type", "severity", "id"}):
            invalid(index, "存在不适用或未知字段")
        rule = dict(original)
        rule.setdefault("severity", "error")
        rule.setdefault("id", f"rule_{index + 1}")
        if rule["severity"] not in ("error", "warning"):
            invalid(index, "severity 仅支持 error/warning")
        identifier = rule["id"]
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 128:
            invalid(index, "id 必须为 1..128 字符的非空字符串")
        if identifier in identifiers:
            invalid(index, "id 必须唯一")
        identifiers.add(identifier)
        for key in RULE_KEYS[kind] - {"min", "max", "values"}:
            value = rule.get(key)
            if key == "fields":
                if (not isinstance(value, list) or not value or len(value) > 4096
                        or not all(isinstance(field, str) and field.strip() for field in value)
                        or len(set(value)) != len(value)):
                    invalid(index, "fields 必须为不重复的非空字段名列表")
            elif not isinstance(value, str) or not value.strip():
                invalid(index, "缺少有效的字段名、类型或操作符")
        if kind == "type" and rule["value_type"] not in VALUE_TYPES:
            invalid(index, "value_type 不支持")
        if kind == "compare" and rule["operator"] not in OPERATORS:
            invalid(index, "operator 不支持")
        if kind == "enum":
            values = rule.get("values")
            if not isinstance(values, list) or not values:
                invalid(index, "values 必须为非空 JSON 值列表")
            try:
                rule["_values"] = {canonical(value) for value in values}
            except (ValueError, TypeError, RecursionError):
                invalid(index, "values 必须为有限、合法的 JSON 值")
        if kind in {"range", "length", "row_count"}:
            if "min" not in rule and "max" not in rule:
                invalid(index, "至少指定 min 或 max")
            for bound in ("min", "max"):
                if bound not in rule:
                    continue
                if kind == "range":
                    try:
                        rule[bound] = numeric(rule[bound])
                    except ValueError:
                        invalid(index, "range 边界必须是有限数字或十进制字符串")
                elif type(rule[bound]) is not int or rule[bound] < 0:
                    invalid(index, "length/row_count 边界必须是非负整数")
            if "min" in rule and "max" in rule and rule["min"] > rule["max"]:
                invalid(index, "min 不得大于 max")
        validated.append(rule)
    return validated


def empty(value: Any) -> bool:
    return value is MISSING or value is None or (isinstance(value, str) and value == "")


def typed(value: Any, kind: str, metadata: str | None) -> bool:
    if value is MISSING or value is None:
        return False
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return type(value) is int
    if kind == "boolean":
        return type(value) is bool
    if kind == "number":
        if isinstance(value, bool) or not (isinstance(value, (int, float, Decimal)) or metadata == "decimal"):
            return False
        try:
            numeric(value)
            return True
        except ValueError:
            return False
    if not isinstance(value, str):
        return False
    try:
        if kind == "date":
            return len(value) == 10 and date.fromisoformat(value).isoformat() == value
        if kind == "datetime":
            if len(value) <= 10 or value[10] not in ("T", " "):
                return False
            datetime.fromisoformat(value)
            return True
    except ValueError:
        pass
    return False


def semantic(value: Any, field: str, types: dict) -> Any:
    return numeric(value) if value is not MISSING and value is not None and types.get(field) == "decimal" else value


def in_bounds(value: Any, rule: dict) -> bool:
    return ("min" not in rule or value >= rule["min"]) and ("max" not in rule or value <= rule["max"])


def compare(left: Any, right: Any, operator: str) -> bool:
    if left is MISSING or right is MISSING or left is None or right is None:
        return False
    if operator in {"eq", "ne"}:
        equal = canonical(left) == canonical(right)
        return equal if operator == "eq" else not equal
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
        left, right = numeric(left), numeric(right)
    elif not (isinstance(left, str) and isinstance(right, str)):
        return False
    # Fixed dispatch only; never eval or dynamically execute supplied operators.
    if operator == "lt":
        return left < right
    if operator == "le":
        return left <= right
    if operator == "gt":
        return left > right
    return left >= right


def rule_fields(rule: dict) -> list[str]:
    if rule["type"] in {"required", "unique"}:
        return rule["fields"]
    if rule["type"] == "compare":
        return [rule["left"], rule["right"]]
    return [rule["field"]] if "field" in rule else []


def check_dataset(dataset: dict, rules: list[dict], max_issues: int) -> dict:
    rows, locations, columns = dataset["rows"], dataset["locations"], dataset["columns"]
    if dataset.get("complete") is not True or len(rows) != len(locations):
        raise PluginError("INCOMPLETE_DATASET", "读取结果不完整，拒绝对抽样数据作全量质量判断")
    types = dataset.get("types", {})
    issues, counts = [], []
    totals = {"errors": 0, "warnings": 0}

    def add(index: int, fields: list[str], location: dict | None, code: str, message: str):
        severity = rules[index]["severity"]
        key = "errors" if severity == "error" else "warnings"
        totals[key] += 1
        counts[index][key] += 1
        if len(issues) < max_issues:
            issues.append({"rule_index": index, "rule_id": rules[index]["id"],
                           "rule_type": rules[index]["type"], "severity": severity,
                           "fields": fields, "location": location, "code": code, "message": message})

    for index, rule in enumerate(rules):
        kind, fields = rule["type"], rule_fields(rule)
        count = {"rule_index": index, "rule_id": rule["id"], "type": kind,
                 "severity": rule["severity"], "checked": 0, "errors": 0, "warnings": 0}
        counts.append(count)
        if kind == "row_count":
            count["checked"] = 1
            if not in_bounds(len(rows), rule):
                add(index, [], None, "ROW_COUNT", "数据行数不在指定范围")
            continue
        # An absent column is a structural quality violation even for zero rows.
        missing_columns = [field for field in fields if field not in columns]
        if missing_columns:
            add(index, missing_columns, None, "MISSING_COLUMN", "数据集缺少规则引用字段")
        seen: set[tuple] = set()
        for row, location in zip(rows, locations):
            count["checked"] += 1
            if kind == "required":
                for field in fields:
                    if empty(row.get(field, MISSING)):
                        add(index, [field], location, "REQUIRED", "字段缺失、null 或空字符串")
                continue
            if kind == "unique":
                values = [row.get(field, MISSING) for field in fields]
                if any(empty(value) for value in values):
                    add(index, fields, location, "EMPTY_KEY", "唯一键存在缺失、null 或空字符串")
                    continue
                try:
                    key = tuple(canonical(semantic(value, field, types)) for field, value in zip(fields, values))
                except (ValueError, TypeError):
                    add(index, fields, location, "INVALID_KEY", "唯一键含不支持或非有限值")
                    continue
                if key in seen:
                    add(index, fields, location, "DUPLICATE_KEY", "组合唯一键与之前记录重复")
                else:
                    seen.add(key)
                continue
            value = row.get(rule.get("field"), MISSING)
            try:
                if kind == "type":
                    valid = typed(value, rule["value_type"], types.get(rule["field"]))
                elif kind == "enum":
                    valid = value is not MISSING and canonical(semantic(value, rule["field"], types)) in rule["_values"]
                elif kind == "range":
                    valid = value is not MISSING and value is not None and in_bounds(numeric(value), rule)
                elif kind == "length":
                    valid = isinstance(value, str) and in_bounds(len(value), rule)
                else:  # validated compare
                    left, right = rule["left"], rule["right"]
                    valid = compare(semantic(row.get(left, MISSING), left, types),
                                    semantic(row.get(right, MISSING), right, types), rule["operator"])
            except (ValueError, TypeError, InvalidOperation, OverflowError):
                valid = False
            if not valid:
                add(index, fields, location, kind.upper(), "字段缺失、值类型无效或不满足规则")
    total = totals["errors"] + totals["warnings"]
    for count in counts:
        count["passed"] = count["errors"] == 0
        count["issues"] = count["errors"] + count["warnings"]
    return {"passed": totals["errors"] == 0, "complete": True, "rows_checked": len(rows),
            **totals, "issue_count": total, "retained_issues": len(issues),
            "omitted_issues": total - len(issues), "truncated": total > len(issues),
            "rule_counts": counts, "issues": issues}


def csv_safe(value: Any) -> str:
    text = str(value)
    # Quoting alone does not neutralize formulas in spreadsheet applications.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def markdown_safe(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\r", " ").replace("\n", " ").replace("`", "&#96;")


def write_reports(context: Any, report: dict) -> list[str]:
    basename = context.task.id
    files = [context.files.write_text(f"{basename}.json", json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    headings = ["record_type", "rule_index", "rule_id", "rule_type", "severity", "fields", "location", "code", "message",
                "checked", "passed", "errors", "warnings", "issue_count", "retained_issues", "omitted_issues", "truncated"]
    writer.writerow(headings)

    def csv_row(record: dict):
        writer.writerow([csv_safe(json.dumps(record[key], ensure_ascii=False, allow_nan=False)
                                  if key in {"fields", "location"} and key in record else record.get(key, ""))
                         for key in headings])

    csv_row({"record_type": "summary", "checked": report["rows_checked"],
             **{key: report[key] for key in ["passed", "errors", "warnings", "issue_count", "retained_issues", "omitted_issues", "truncated"]}})
    for count in report["rule_counts"]:
        csv_row({**count, "record_type": "rule", "rule_type": count["type"], "issue_count": count["issues"]})
    for issue in report["issues"]:
        csv_row({**issue, "record_type": "issue"})
    files.append(context.files.write_text(f"{basename}.csv", stream.getvalue(), encoding="utf-8-sig"))
    lines = ["# 数据质量报告", "", f"- 任务：{markdown_safe(basename)}",
             f"- passed：{str(report['passed']).lower()}", f"- 完整检查行数：{report['rows_checked']}",
             f"- errors：{report['errors']}；warnings：{report['warnings']}",
             f"- truncated：{str(report['truncated']).lower()}；明细保留 {report['retained_issues']} / {report['issue_count']}；省略 {report['omitted_issues']}",
             "", "## 规则计数", "", "|规则|类型|级别|检查次数|errors|warnings|", "|---|---|---|---:|---:|---:|"]
    for count in report["rule_counts"]:
        lines.append("|" + "|".join(markdown_safe(count[key]) for key in ["rule_id", "type", "severity", "checked", "errors", "warnings"]) + "|")
    if report["input_warnings"]:
        lines.extend(["", "## 读取提示", "", "读取/标准化存在提示，详情参见 JSON 的 input_warnings；不计入质量规则 warnings。"])
    lines.extend(["", "## 明细（无原值）", "", "|规则|级别|字段|源位置|原因|", "|---|---|---|---|---|"])
    for issue in report["issues"]:
        lines.append("|" + "|".join(markdown_safe(value) for value in [issue["rule_id"], issue["severity"],
                      json.dumps(issue["fields"], ensure_ascii=False), json.dumps(issue["location"], ensure_ascii=False), issue["message"]]) + "|")
    files.append(context.files.write_text(f"{basename}.md", "\n".join(lines) + "\n"))
    return files


class Plugin:
    def init(self, context):
        self.context = context

    def execute(self, command, params):
        if command != "data.check":
            raise PluginError("COMMAND_NOT_FOUND", "数据质量插件不支持此命令")
        rules = validate_params(params)  # All rules validated before any file read.
        try:
            dataset = normalize_dataset(read_dataset(params["input"], params.get("options", {})), params.get("normalize", {}))
        except PluginError:
            # Shared SDK diagnostics are value-free; preserve actionable errors.
            raise
        except (ValueError, TypeError, OverflowError, OSError):
            raise PluginError("INPUT_INVALID", "输入读取或标准化失败") from None
        report = check_dataset(dataset, rules, params.get("max_issues", 1000))
        report.update({"task_id": self.context.task.id, "format": dataset["format"],
                       "source": dataset["source"], "columns": dataset["columns"],
                       "types": dataset.get("types", {}), "input_warnings": dataset.get("warnings", [])})
        files = write_reports(self.context, report)
        summary = {key: value for key, value in report.items() if key not in {"issues", "source", "columns", "types", "input_warnings"}}
        # Counts only in logs; no original records, enum values or keys.
        self.context.logger.info(f"质量检查完成：rows={report['rows_checked']} errors={report['errors']} warnings={report['warnings']}")
        warnings = []
        if report["warnings"]:
            warnings.append(f"质量规则 warning：{report['warnings']} 项")
        if report["truncated"]:
            warnings.append(f"明细已截断，省略 {report['omitted_issues']} 项；仍已检查全量数据")
        if report["input_warnings"]:
            warnings.append("输入读取或标准化包含提示，详见 JSON 报告")
        return Result("success", f"数据质量{'通过' if report['passed'] else '未通过'}；错误 {report['errors']}，警告 {report['warnings']}", summary, files, warnings)

    def destroy(self):
        pass
