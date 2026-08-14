from __future__ import annotations

import csv, io, json, re
from pathlib import Path

from testbox.sdk import PluginError, Result

CREATE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?([\w.]+)[`\"\]]?\s*\((.*?)\)\s*(?:COMMENT\s*[='\s]+([^;]+))?\s*;", re.I | re.S)
FIELD_RE = re.compile(r"^\s*[`\"\[]?([\w]+)[`\"\]]?\s+([A-Z]+(?:\s*\([^)]*\))?)(.*)$", re.I | re.S)

def split_fields(body: str) -> list[str]:
    result, current, depth, quote = [], [], 0, None
    for character in body:
        if character in "'\"" and (not current or current[-1] != "\\"):
            quote = None if quote == character else character if quote is None else quote
        elif not quote:
            depth += character == "("; depth -= character == ")"
        if character == "," and depth == 0 and not quote: result.append("".join(current)); current = []
        else: current.append(character)
    if current: result.append("".join(current))
    return result

class Plugin:
    def init(self, context): self.context = context
    def execute(self, command, params):
        source = Path(params["input"])
        if not source.is_file(): raise PluginError("INPUT_NOT_FOUND", "SQL 输入文件不存在")
        tables = CREATE_RE.findall(source.read_text(encoding="utf-8"))
        if not tables: raise PluginError("INPUT_INVALID", "未找到支持的 CREATE TABLE 语句")
        rows = []
        for table, body, table_comment in tables:
            for definition in split_fields(body):
                match = FIELD_RE.match(definition)
                if not match or match.group(1).upper() in {"PRIMARY", "KEY", "UNIQUE", "CONSTRAINT", "INDEX"}: continue
                tail = match.group(3); comment = re.search(r"COMMENT\s+['\"]([^'\"]+)", tail, re.I)
                rows.append({"table": table, "field": match.group(1), "type": match.group(2).upper(), "nullable": "NOT NULL" not in tail.upper(), "comment": comment.group(1) if comment else "", "table_comment": table_comment.strip(" '\"")})
        if not rows: raise PluginError("INPUT_INVALID", "未解析到字段定义")
        if params["format"] == "json": name = "fields.json"; self.context.files.write_text(name, json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            name = "fields.csv"; buffer = io.StringIO(); writer = csv.DictWriter(buffer, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows); self.context.files.write_text(name, buffer.getvalue())
        return Result("success", f"已解析 {len(rows)} 个字段", {"field_count": len(rows)}, [name])
    def destroy(self): pass
