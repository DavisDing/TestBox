"""Task result and report persistence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from testbox.core.manifest import Manifest
from testbox.sdk import Result


def write_json(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_report(path: Path, task_id: str, manifest: Manifest, result: Result) -> None:
    files = "\n".join(f"- `output/{item}`" for item in result.files) or "- 无"
    path.write_text(f"# TestBox 任务报告\n\n- 任务 ID: `{task_id}`\n- 插件: `{manifest.name}` {manifest.version}\n- 状态: {result.status}\n- 摘要: {result.message}\n\n## 产物\n{files}\n", encoding="utf-8")
