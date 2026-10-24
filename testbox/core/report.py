"""Task result and report persistence."""
from __future__ import annotations

import json
import html
from pathlib import Path
from typing import Any

from testbox.core.manifest import Manifest
from testbox.sdk import Result


def write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def markdown_text(value: Any) -> str:
    """Render untrusted values as one plain Markdown text fragment."""
    text = html.escape(str(value), quote=True)
    return "".join(f"&#{ord(char)};" if char in "\\`*_{}[]()#+!|~\r\n" else char for char in text)


def write_report(path: Path, task_id: str, manifest: Manifest, result: Result) -> None:
    files = "\n".join(f"- {markdown_text('output/' + item)}" for item in result.files) or "- 无"
    warnings = "\n".join(f"- {markdown_text(item)}" for item in result.warnings) or "- 无"
    path.write_text(
        f"# TestBox 任务报告\n\n- 任务 ID: {markdown_text(task_id)}\n"
        f"- 插件: {markdown_text(manifest.name)} {markdown_text(manifest.version)}\n"
        f"- 状态: {markdown_text(result.status)}\n- 摘要: {markdown_text(result.message)}\n"
        f"\n## 产物\n{files}\n\n## 警告\n{warnings}\n", encoding="utf-8")
