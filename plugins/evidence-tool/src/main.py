from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from testbox.sdk import PluginError, Result

ROLES = {
    "测试名称": ("测试名称", "用例名", "用例名称", "测试用例", "case name", "test name", "name"),
    "验证点": ("验证点", "检查点", "校验点", "检查项", "checkpoint", "check point"),
    "步骤名称": ("步骤名称", "步骤", "操作步骤", "step name", "step", "步骤标题"),
    "步骤描述": ("步骤描述", "步骤说明", "操作说明", "描述", "step description", "desc"),
    "预期结果": ("预期结果", "期望结果", "expected result", "expected", "期望"),
    "测试结果": ("测试结果", "结果", "状态", "执行结果", "result", "status", "pass/fail"),
}
REQUIRED = ("测试名称", "验证点", "测试结果")
EXECUTED = {"已执行", "pass", "passed", "通过", "done", "completed", "完成", "成功"}
NORMALIZE_RE = re.compile(r"[\s\-_·./\\|:：,，。；;()（）\[\]{}<>《》\"'`~!@#$%^&*=+?]+")
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def normalize(value: object) -> str:
    return NORMALIZE_RE.sub("", str(value or "").strip().lower())


def suggest_mapping(headers: list[str]) -> dict[str, str | None]:
    normalized = {normalize(header): header for header in headers}
    result: dict[str, str | None] = {}
    for role, aliases in ROLES.items():
        match = next((normalized[normalize(alias)] for alias in aliases if normalize(alias) in normalized), None)
        if match is None:
            for header in headers:
                probe = normalize(header)
                if probe and any(normalize(alias) in probe or probe in normalize(alias) for alias in aliases):
                    match = header
                    break
        result[role] = match
    return result


@dataclass(frozen=True)
class TestItem:
    row_index: int
    case_name: str
    checkpoint: str
    step_name: str = ""
    step_description: str = ""
    expected_result: str = ""

    def step_note(self) -> str:
        pairs = (("步骤名称", self.step_name), ("步骤描述", self.step_description), ("预期结果", self.expected_result))
        return "\n".join(f"{label}：{value}" for label, value in pairs if value)


class WorkbookCases:
    def __init__(self, path: Path, mapping: dict | None, scan_rows: int):
        try:
            from openpyxl import load_workbook
        except ModuleNotFoundError as error:
            raise PluginError("DEPENDENCY_MISSING", "Evidence Tool 需要 openpyxl，请安装插件 requirements.txt") from error
        try:
            self.workbook = load_workbook(path, keep_vba=path.suffix.lower() == ".xlsm")
        except Exception as error:
            raise PluginError("INPUT_INVALID", "无法读取 Excel，仅支持有效的 .xlsx 或 .xlsm 文件") from error
        self.sheet = self.workbook.active
        self.header_row = self._detect_header(scan_rows)
        self.headers = self._headers()
        selected = mapping or suggest_mapping(self.headers)
        positions = {header: index + 1 for index, header in enumerate(self.headers)}
        self.columns = {role: positions[header] for role, header in selected.items() if header in positions}
        missing = [role for role in REQUIRED if role not in self.columns]
        if missing:
            raise PluginError("COLUMN_MAPPING_REQUIRED", f"Excel 缺少必要列映射：{'、'.join(missing)}", details={"headers": self.headers, "suggested_mapping": suggest_mapping(self.headers)})
        self.mapping = {role: self.headers[column - 1] for role, column in self.columns.items()}
        self._merged: dict[tuple[int, int], tuple[int, int]] = {}

    def _detect_header(self, scan_rows: int) -> int:
        aliases = {normalize(alias) for values in ROLES.values() for alias in values}
        best = (0, 1)
        for row in range(1, min(self.sheet.max_row, scan_rows) + 1):
            values = [normalize(self.sheet.cell(row, col).value) for col in range(1, self.sheet.max_column + 1)]
            nonempty = [value for value in values if value]
            if len(nonempty) >= 2:
                score = len(nonempty) + 5 * len(set(nonempty) & aliases)
                if score > best[0]: best = (score, row)
        return best[1]

    def _headers(self) -> list[str]:
        result, used = [], set()
        for column in range(1, self.sheet.max_column + 1):
            value = self.sheet.cell(self.header_row, column).value
            header = str(value).strip() if value not in (None, "") else f"第{column}列"
            if header in used: header = f"{header}({column})"
            used.add(header); result.append(header)
        return result

    def _parent(self, row: int, column: int) -> tuple[int, int]:
        key = (row, column)
        if key not in self._merged:
            self._merged[key] = next(((area.min_row, area.min_col) for area in self.sheet.merged_cells.ranges if area.min_row <= row <= area.max_row and area.min_col <= column <= area.max_col), key)
        return self._merged[key]

    def text(self, row: int, role: str, merged: bool = False) -> str:
        if role not in self.columns: return ""
        column = self.columns[role]
        value = self.sheet.cell(row, column).value
        if value in (None, "") and merged:
            value = self.sheet.cell(*self._parent(row, column)).value
        return str(value).strip() if value not in (None, "") else ""

    def pending(self) -> list[TestItem]:
        result, current_case, current_checkpoint = [], "", ""
        for row in range(self.header_row + 1, self.sheet.max_row + 1):
            raw_case, raw_checkpoint = self.text(row, "测试名称", True), self.text(row, "验证点", True)
            if raw_case and raw_case != current_case:
                current_case, current_checkpoint = raw_case, raw_checkpoint or ""
            elif raw_case: current_case = raw_case
            if raw_checkpoint: current_checkpoint = raw_checkpoint
            step = (self.text(row, "步骤名称"), self.text(row, "步骤描述"), self.text(row, "预期结果"))
            if not any((current_case, current_checkpoint, *step)): continue
            if self.text(row, "测试结果").lower() in EXECUTED: continue
            result.append(TestItem(row, current_case or f"未命名用例_{row}", current_checkpoint or "未填写验证点", *step))
        return result

    def mark(self, rows: list[int], status: str) -> None:
        column = self.columns["测试结果"]
        for row in rows:
            self.sheet.cell(*self._parent(row, column)).value = status

    def save(self, path: Path) -> None:
        self.workbook.save(path)

    def close(self) -> None:
        self.workbook.close()


def safe_filename(name: str) -> str:
    return (INVALID_FILENAME_RE.sub("_", name).strip(" ._")[:80] or "未命名用例")


def report_names(case_names: list[str]) -> dict[str, str]:
    """Produce stable readable names without cross-task collisions."""
    return {case_name: f"{safe_filename(case_name)[:71]}-{hashlib.sha256(case_name.encode('utf-8')).hexdigest()[:8]}.docx" for case_name in case_names}


def build_word(path: Path, case_name: str, entries: list[tuple[TestItem, Path]], width: float, existing: Path | None = None) -> None:
    try:
        from docx import Document
        from docx.shared import Inches
    except ModuleNotFoundError as error:
        raise PluginError("DEPENDENCY_MISSING", "Evidence Tool 需要 python-docx，请安装插件 requirements.txt") from error
    doc = Document(str(existing)) if existing else Document()
    if not existing: doc.add_heading(case_name, level=1)
    checkpoints = {paragraph.text.removeprefix("验证点：").strip() for paragraph in doc.paragraphs if paragraph.text.strip().startswith("验证点：")}
    for item, image in entries:
        if item.checkpoint not in checkpoints:
            doc.add_paragraph(f"验证点：{item.checkpoint}"); checkpoints.add(item.checkpoint)
        for line in item.step_note().splitlines():
            if line.strip(): doc.add_paragraph(line.strip())
        try: doc.add_picture(str(image), width=Inches(width))
        except Exception as error: raise PluginError("INPUT_INVALID", f"无法读取截图：{image.name}") from error
        doc.add_paragraph()
    temporary = path.with_suffix(path.suffix + ".tmp")
    doc.save(temporary)
    temporary.replace(path)


class Plugin:
    def init(self, context):
        self.context = context

    def execute(self, command, params):
        cases = WorkbookCases(Path(params["input"]), params.get("column_mapping"), int(self.context.config.get("header_scan_rows", 15)))
        try:
            items = cases.pending()
            if command == "evidence.inspect":
                name = "pending-items.json"
                payload = {"sheet": cases.sheet.title, "header_row": cases.header_row, "mapping": cases.mapping, "mode": "步骤版" if any(role in cases.columns for role in ("步骤名称", "步骤描述", "预期结果")) else "基础版", "pending_count": len(items), "items": [asdict(item) for item in items]}
                self.context.files.write_text(name, json.dumps(payload, ensure_ascii=False, indent=2))
                return Result("success", f"识别到 {len(items)} 条待执行项", payload, [name])
            if command != "evidence.build": raise PluginError("INVALID_PARAMS", "不支持的命令")
            screenshots = [Path(path) for path in params["screenshots"]]
            requested_rows = params.get("row_indexes")
            if requested_rows is not None:
                if len(requested_rows) != len(screenshots):
                    raise PluginError("INVALID_PARAMS", "row_indexes 必须与 screenshots 数量一致")
                if len(set(requested_rows)) != len(requested_rows):
                    raise PluginError("INVALID_PARAMS", "row_indexes 不能包含重复行号")
                by_row = {item.row_index: item for item in items}
                missing_rows = [row for row in requested_rows if row not in by_row]
                if missing_rows:
                    raise PluginError("INPUT_CHANGED", f"Excel 已发生变化或行已执行：{missing_rows}")
                selected_items = [by_row[row] for row in requested_rows]
            else:
                selected_items = items[:len(screenshots)]
            count = min(len(selected_items), len(screenshots))
            if count == 0: raise PluginError("INPUT_INVALID", "没有可关联的待执行项或截图")
            if requested_rows is None and len(screenshots) != len(items) and not params.get("include_unmatched", False):
                raise PluginError("SCREENSHOT_COUNT_MISMATCH", f"待执行项 {len(items)} 条，截图 {len(screenshots)} 张，请保持数量一致")
            grouped: dict[str, list[tuple[TestItem, Path]]] = {}
            for item, screenshot in zip(selected_items[:count], screenshots[:count]): grouped.setdefault(item.case_name, []).append((item, screenshot))
            files = []
            names = report_names(list(grouped))
            existing_reports = {Path(path).name: Path(path) for path in params.get("existing_reports", [])}
            for case_name, entries in grouped.items():
                relative = f"reports/{names[case_name]}"
                target = self.context.files.resolve(relative); target.parent.mkdir(parents=True, exist_ok=True)
                build_word(target, case_name, entries, float(self.context.config.get("image_width_inches", 5.8)), existing_reports.get(names[case_name])); files.append(relative)
            if params.get("update_excel", True):
                cases.mark([item.row_index for item in selected_items[:count]], params.get("status") or self.context.config.get("executed_status", "已执行"))
                relative = f"executed-{Path(params['input']).name}"
                cases.save(self.context.files.resolve(relative)); files.append(relative)
            index = "evidence-index.json"
            summary = {"case_count": len(grouped), "evidence_count": count, "pending_count": len(items) - count, "mapping": cases.mapping, "rows": [item.row_index for item in selected_items[:count]]}
            self.context.files.write_text(index, json.dumps(summary, ensure_ascii=False, indent=2)); files.append(index)
            return Result("success", f"已生成 {len(grouped)} 份报告，写入 {count} 张截图", summary, files, [f"尚有 {len(items) - count} 条未生成证据"] if len(items) > count else [])
        finally:
            cases.close()

    def destroy(self):
        pass
