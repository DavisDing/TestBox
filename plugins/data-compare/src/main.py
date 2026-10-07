"""Read-only, typed dataset comparison using only the public TestBox SDK."""
from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict, deque
from decimal import Decimal
from fractions import Fraction
from typing import Any

from testbox.sdk import PluginError, Result, normalize_dataset, read_dataset

MISSING = object()
# Tolerance is not an equivalence relation: multiset requires maximum matching,
# not a greedy match or rounded hashes. Bound expensive ambiguous comparisons.
MAX_MATCH_WORK = 2_000_000
MAX_REPORT_BYTES = 64 * 1024 * 1024


def _invalid(message: str, code: str = "INVALID_PARAMS", **details: Any) -> PluginError:
    return PluginError(code, message, details=details)


def _names(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise _invalid(f"{label} 必须为非空字段名列表")
    if len(value) != len(set(value)):
        raise _invalid(f"{label} 不得包含重复字段名")
    return value


def _tolerance(value: Any, label: str) -> Fraction:
    if type(value) not in (int, float):
        raise _invalid(f"{label} 必须为有限非负数字")
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise _invalid(f"{label} 必须为有限非负数字")
    return Fraction(number)


def _identity(value: Any, kind: str | None = None) -> tuple:
    """Type-tag every nested value, keeping absence distinct from null."""
    if value is MISSING:
        return ("missing",)
    if value is None:
        return ("null",)
    if isinstance(value, dict):
        return ("object", tuple(sorted((k, _identity(v)) for k, v in value.items())))
    if isinstance(value, list):
        return ("array", tuple(_identity(v) for v in value))
    if kind == "decimal" and isinstance(value, str):
        return ("decimal", value)
    return (type(value).__name__, value)


def _numeric(value: Any, kind: str | None) -> Fraction | None:
    if type(value) in (int, float) or (kind == "decimal" and isinstance(value, str)):
        number = Decimal(str(value))
        if number.is_finite():
            return Fraction(number)
    return None


def _same(lv: Any, rv: Any, lt: str | None, rt: str | None,
          absolute: Fraction, relative: Fraction) -> bool:
    if _identity(lv, lt) == _identity(rv, rt):
        return True
    if not (absolute or relative):
        return False
    a, b = _numeric(lv, lt), _numeric(rv, rt)
    return a is not None and b is not None and abs(a - b) <= max(absolute, relative * max(abs(a), abs(b)))


def _read(path: Any, options: Any, rules: Any) -> dict:
    if not isinstance(path, str) or not path:
        raise _invalid("输入文件路径必须为非空字符串")
    if not isinstance(options, dict) or not isinstance(rules, dict):
        raise _invalid("读取选项与标准化规则必须为对象")
    # Delegate defaults unchanged: the same file/options/rules must have the
    # same meaning in data.preview and data.compare.
    dataset = normalize_dataset(read_dataset(path, options), rules)
    if dataset.get("complete") is not True or len(dataset["rows"]) != len(dataset["locations"]):
        raise _invalid("SDK 返回不完整数据集，拒绝按截断数据判断相等", "INPUT_INVALID")
    return dataset


def _location(dataset: dict, index: int) -> dict:
    return {"source": dataset["source"], **dataset["locations"][index]}


class Differences:
    """Count the entire comparison, retaining only a bounded detail prefix."""
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.total = 0
        self.counts: Counter = Counter()
        self.items: list[dict] = []

    def add(self, kind: str, **fields: Any) -> None:
        self.total += 1
        self.counts[kind] += 1
        if len(self.items) < self.maximum:
            self.items.append({"kind": kind, **fields})


def _index(dataset: dict, keys: list[str], side: str) -> dict[tuple, int]:
    absent = set(keys) - set(dataset["columns"])
    if absent:
        raise _invalid(f"{side} 缺少主键列: {', '.join(sorted(absent))}", "KEY_INVALID", side=side)
    indexed: dict[tuple, int] = {}
    types = dataset.get("types", {})
    for index, row in enumerate(dataset["rows"]):
        values = []
        for key in keys:
            value = row.get(key, MISSING)
            if value is MISSING or value is None or (isinstance(value, str) and not value.strip()):
                raise _invalid(f"{side} 主键 {key} 缺失或为空", "KEY_INVALID", side=side, location=_location(dataset, index))
            if isinstance(value, (dict, list)):
                raise _invalid(f"{side} 主键 {key} 必须为标量", "KEY_INVALID", side=side, location=_location(dataset, index))
            values.append(_identity(value, types.get(key)))
        token = tuple(values)
        if token in indexed:
            raise _invalid(f"{side} 主键重复或标准化后冲突", "KEY_DUPLICATE", side=side,
                           first_location=_location(dataset, indexed[token]), location=_location(dataset, index))
        indexed[token] = index
    return indexed


def _record_change(diffs: Differences, side: str, dataset: dict, index: int, columns: list[str], key: dict | None = None) -> None:
    # Added = present only on right, missing = present only on left.
    diffs.add("added_row" if side == "right" else "missing_row", key=key,
              **{side: {c: v for c, v in dataset["rows"][index].items() if c in columns},
                 f"{side}_location": _location(dataset, index)})


def _paired(diffs: Differences, left: dict, right: dict, li: int, ri: int, columns: list[str],
            absolute: Fraction, relative: Fraction, key: dict | None = None) -> None:
    lrow, rrow = left["rows"][li], right["rows"][ri]
    for column in columns:
        lv, rv = lrow.get(column, MISSING), rrow.get(column, MISSING)
        lt, rt = left.get("types", {}).get(column), right.get("types", {}).get(column)
        if _same(lv, rv, lt, rt, absolute, relative):
            continue
        fields = {"column": column, "key": key, "left_present": lv is not MISSING,
                  "right_present": rv is not MISSING, "left_type": _identity(lv, lt)[0],
                  "right_type": _identity(rv, rt)[0], "left_location": _location(left, li),
                  "right_location": _location(right, ri)}
        if lv is not MISSING:
            fields["left"] = lv
        if rv is not MISSING:
            fields["right"] = rv
        diffs.add("field_changed", **fields)


def _signature(dataset: dict, index: int, columns: list[str], numeric_wildcard: bool = False) -> tuple:
    row, types = dataset["rows"][index], dataset.get("types", {})
    return tuple(("numeric",) if numeric_wildcard and _numeric(row.get(c, MISSING), types.get(c)) is not None
                 else _identity(row.get(c, MISSING), types.get(c)) for c in columns)


def _maximum_matching(left_indices: list[int], right_indices: list[int], left: dict, right: dict,
                      columns: list[str], absolute: Fraction, relative: Fraction, budget: list[int]) -> tuple[set[int], set[int]]:
    """Iterative augmenting paths handle non-transitive tolerance correctly."""
    graph = {}
    for li in left_indices:
        adjacent = []
        for ri in right_indices:
            budget[0] += max(1, len(columns))
            if budget[0] > MAX_MATCH_WORK:
                raise _invalid("多重集容差候选/匹配计算超过 2000000 次限制；请按非数值字段分组或缩小数据集", "COMPARE_LIMIT_EXCEEDED")
            if all(_same(left["rows"][li].get(c, MISSING), right["rows"][ri].get(c, MISSING),
                         left.get("types", {}).get(c), right.get("types", {}).get(c), absolute, relative) for c in columns):
                adjacent.append(ri)
        graph[li] = adjacent
    match_left: dict[int, int] = {}
    match_right: dict[int, int] = {}
    for start in left_indices:
        queue = deque([start])
        seen_left = {start}
        parent_right: dict[int, int] = {}
        end = None
        while queue and end is None:
            li = queue.popleft()
            for ri in graph[li]:
                budget[0] += 1
                if budget[0] > MAX_MATCH_WORK:
                    raise _invalid("多重集容差匹配计算超过 2000000 次限制", "COMPARE_LIMIT_EXCEEDED")
                if ri in parent_right:
                    continue
                parent_right[ri] = li
                if ri not in match_right:
                    end = ri
                    break
                next_left = match_right[ri]
                if next_left not in seen_left:
                    seen_left.add(next_left)
                    queue.append(next_left)
        if end is not None:
            while True:
                li = parent_right[end]
                previous = match_left.get(li)
                match_left[li], match_right[end] = end, li
                if previous is None:
                    break
                end = previous
    return set(match_left), set(match_right)


def _multiset(diffs: Differences, left: dict, right: dict, columns: list[str], absolute: Fraction, relative: Fraction) -> int:
    lgroups: dict[tuple, list[int]] = defaultdict(list)
    rgroups: dict[tuple, list[int]] = defaultdict(list)
    tolerant = bool(absolute or relative)
    for index in range(len(left["rows"])):
        lgroups[_signature(left, index, columns, tolerant)].append(index)
    for index in range(len(right["rows"])):
        rgroups[_signature(right, index, columns, tolerant)].append(index)
    matched_left: set[int] = set()
    matched_right: set[int] = set()
    budget = [0]
    for signature, lis in lgroups.items():
        ris = rgroups.get(signature, [])
        if not ris:
            continue
        if not tolerant:
            count = min(len(lis), len(ris))
            matched_left.update(lis[:count])
            matched_right.update(ris[:count])
            continue
        # Homogeneous groups admit a direct count comparison. Do NOT cancel
        # exact rows in heterogeneous groups: that can lose a perfect tolerance
        # matching (e.g. left 1,2 and right 2,3 with absolute tolerance 1).
        ls, rs = _signature(left, lis[0], columns), _signature(right, ris[0], columns)
        if (all(_signature(left, i, columns) == ls for i in lis)
                and all(_signature(right, i, columns) == rs for i in ris)):
            if all(_same(left["rows"][lis[0]].get(c, MISSING), right["rows"][ris[0]].get(c, MISSING),
                         left.get("types", {}).get(c), right.get("types", {}).get(c), absolute, relative) for c in columns):
                count = min(len(lis), len(ris))
                matched_left.update(lis[:count])
                matched_right.update(ris[:count])
            continue
        ml, mr = _maximum_matching(lis, ris, left, right, columns, absolute, relative, budget)
        matched_left.update(ml)
        matched_right.update(mr)
    for index in range(len(left["rows"])):
        if index not in matched_left:
            _record_change(diffs, "left", left, index, columns)
    for index in range(len(right["rows"])):
        if index not in matched_right:
            _record_change(diffs, "right", right, index, columns)
    return len(matched_left)


def compare(left: dict, right: dict, params: dict) -> dict:
    mode = params.get("mode", "position")
    if mode not in ("position", "key", "multiset"):
        raise _invalid("mode 必须为 position、key 或 multiset")
    keys = _names(params.get("keys", []), "keys")
    ignored = set(_names(params.get("ignore_columns", []), "ignore_columns"))
    if mode == "key" and (not keys or ignored.intersection(keys)):
        raise _invalid("key 模式必须提供 keys，且主键不能被忽略")
    if mode != "key" and keys:
        raise _invalid("keys 仅适用于 key 模式")
    absolute = _tolerance(params.get("absolute_tolerance", 0), "absolute_tolerance")
    relative = _tolerance(params.get("relative_tolerance", 0), "relative_tolerance")
    maximum = params.get("max_differences", 1000)
    if type(maximum) is not int or not 1 <= maximum <= 100000:
        raise _invalid("max_differences 必须为 1 至 100000 的整数")
    lcols, rcols = [c for c in left["columns"] if c not in ignored], [c for c in right["columns"] if c not in ignored]
    unknown = ignored - (set(left["columns"]) | set(right["columns"]))
    if unknown:
        raise _invalid(f"ignore_columns 引用了不存在的字段: {', '.join(sorted(unknown))}")
    columns = list(dict.fromkeys(lcols + rcols))
    diffs = Differences(maximum)
    for column in lcols:
        if column not in rcols:
            diffs.add("missing_column", column=column, left_source=left["source"], right_source=right["source"])
    for column in rcols:
        if column not in lcols:
            diffs.add("added_column", column=column, left_source=left["source"], right_source=right["source"])
    matched = 0
    if mode == "position":
        matched = min(len(left["rows"]), len(right["rows"]))
        for index in range(matched):
            _paired(diffs, left, right, index, index, columns, absolute, relative)
        for index in range(matched, len(left["rows"])):
            _record_change(diffs, "left", left, index, columns)
        for index in range(matched, len(right["rows"])):
            _record_change(diffs, "right", right, index, columns)
    elif mode == "key":
        lm, rm = _index(left, keys, "left"), _index(right, keys, "right")
        for token, li in lm.items():
            key = {c: left["rows"][li][c] for c in keys}
            if token in rm:
                matched += 1
                _paired(diffs, left, right, li, rm[token], columns, absolute, relative, key)
            else:
                _record_change(diffs, "left", left, li, columns, key)
        for token, ri in rm.items():
            if token not in lm:
                _record_change(diffs, "right", right, ri, columns, {c: right["rows"][ri][c] for c in keys})
    else:
        matched = _multiset(diffs, left, right, columns, absolute, relative)
    return {"equal": diffs.total == 0, "mode": mode, "keys": keys, "ignore_columns": sorted(ignored),
            "absolute_tolerance": params.get("absolute_tolerance", 0), "relative_tolerance": params.get("relative_tolerance", 0),
            "left_rows": len(left["rows"]), "right_rows": len(right["rows"]), "matched_rows": matched,
            "added_rows": diffs.counts["added_row"], "missing_rows": diffs.counts["missing_row"],
            "changed_fields": diffs.counts["field_changed"],
            "column_differences": diffs.counts["missing_column"] + diffs.counts["added_column"],
            "difference_count": diffs.total, "reported_differences": len(diffs.items), "max_differences": maximum,
            "truncated": diffs.total > maximum, "differences": diffs.items,
            "sources": {"left": {"source": left["source"], "format": left["format"], "columns": left["columns"], "types": left.get("types", {}), "normalization": left.get("normalization", {})},
                        "right": {"source": right["source"], "format": right["format"], "columns": right["columns"], "types": right.get("types", {}), "normalization": right.get("normalization", {})}}}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _safe_csv(value: Any) -> str:
    text = _json(value) if not isinstance(value, str) else value
    # Account for whitespace/control prefixes that some spreadsheet importers
    # strip before recognizing a formula. Quote the ORIGINAL text, not a trim.
    if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _bounded(text: str) -> str:
    if len(text.encode("utf-8")) > MAX_REPORT_BYTES:
        raise _invalid("单个比对报告超过 64 MiB；请降低 max_differences", "OUTPUT_LIMIT_EXCEEDED")
    return text


def _markdown(value: Any) -> str:
    text = _json(value)
    # Encode HTML/backticks too: dataset values must remain text, not active
    # report markup or additional table rows.
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("|", "&#124;").replace("`", "&#96;"))


def _reports(context: Any, report: dict) -> list[str]:
    task = context.task.id
    report = {"task_id": task, **report}
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2, allow_nan=False)
    chunks: list[str] = []
    size = 0
    for chunk in encoder.iterencode(report):
        size += len(chunk.encode("utf-8"))
        if size > MAX_REPORT_BYTES:
            raise _invalid("JSON 比对报告超过 64 MiB；请降低 max_differences", "OUTPUT_LIMIT_EXCEEDED")
        chunks.append(chunk)
    json_text = "".join(chunks)
    stream = io.StringIO()
    fields = ["kind", "column", "key", "left_present", "right_present", "left_type", "right_type", "left", "right", "left_location", "right_location"]
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    lines = [f"# 数据比对报告 `{task}`", "", f"- 结果：{'相等' if report['equal'] else '存在差异'}",
             f"- 模式：`{report['mode']}`", f"- 左侧/右侧行数：{report['left_rows']} / {report['right_rows']}",
             f"- 全量差异数：{report['difference_count']}", f"- 已登记明细：{report['reported_differences']}",
             f"- 明细截断：{'是（相等判断仍基于全量数据）' if report['truncated'] else '否'}", ""]
    for warning in report["warnings"]:
        lines.append(f"- 警告：{_markdown(warning)}")
    lines += ["", "## 差异明细", "", "| 类型 | 列 | 左侧 | 右侧 | 左侧位置 | 右侧位置 |", "|---|---|---|---|---|---|"]
    for item in report["differences"]:
        writer.writerow({key: _safe_csv(item[key]) if key in item else "" for key in fields})
        lines.append("| " + " | ".join(_markdown(item[key]) if key in item else "（缺失）"
                                       for key in ("kind", "column", "left", "right", "left_location", "right_location")) + " |")
    texts = [json_text, _bounded(stream.getvalue()), _bounded("\n".join(lines) + "\n")]
    names = [f"{task}.json", f"{task}.csv", f"{task}.md"]
    for name, text in zip(names, texts):
        context.files.write_text(name, text)
    return names


class Plugin:
    def init(self, context: Any) -> None:
        self.context = context

    def execute(self, command: str, params: dict) -> Result:
        if command != "data.compare":
            raise _invalid("不支持的命令")
        left = _read(params.get("left"), params.get("left_options", {}), params.get("left_normalize", {}))
        right = _read(params.get("right"), params.get("right_options", {}), params.get("right_normalize", {}))
        report = compare(left, right, params)
        warnings = [f"左侧：{w}" for w in left["warnings"]] + [f"右侧：{w}" for w in right["warnings"]]
        if report["truncated"]:
            warnings.append(f"差异明细已截断为 {report['reported_differences']} 条；equal 与差异计数依据全量比较")
        report["warnings"] = warnings
        files = _reports(self.context, report)
        # Keep Host/SQLite result bounded; complete (bounded) details live in
        # registered artifacts, not a potentially multi-megabyte Host response.
        summary = {key: value for key, value in report.items() if key != "differences"}
        summary["output_files"] = files
        self.context.logger.info(f"已比较 {report['left_rows']} / {report['right_rows']} 行，差异 {report['difference_count']} 条")
        return Result("success", "数据相等" if report["equal"] else "数据存在差异", data=summary, files=files, warnings=warnings)

    def destroy(self) -> None:
        pass
