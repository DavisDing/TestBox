"""SQLite-backed task history with terminal-state protection."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class TaskHistory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS task_history (
                id TEXT PRIMARY KEY, plugin_name TEXT NOT NULL, plugin_version TEXT NOT NULL,
                command TEXT NOT NULL, params TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL, result_path TEXT NOT NULL,
                workspace_path TEXT NOT NULL, error_code TEXT, heartbeat_at TEXT, host_pid INTEGER
            )
        """)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create(self, record: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO task_history (id, plugin_name, plugin_version, command, params, started_at, status, result_path, workspace_path, heartbeat_at, host_pid) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)",
            (record["id"], record["plugin_name"], record["plugin_version"], record["command"], json.dumps(record["params"], ensure_ascii=False), record["started_at"], record["result_path"], record["workspace_path"], record["started_at"], record["host_pid"]),
        )
        self.connection.execute("UPDATE task_history SET status = 'RUNNING' WHERE id = ? AND status = 'PENDING'", (record["id"],)); self.connection.commit()

    def finish(self, task_id: str, *, status: str, finished_at: str, error_code: str | None = None) -> None:
        self.connection.execute("UPDATE task_history SET status = ?, finished_at = ?, error_code = ?, heartbeat_at = ? WHERE id = ? AND status = 'RUNNING'", (status, finished_at, error_code, finished_at, task_id))
        self.connection.commit()

    def abandon_incomplete(self, finished_at: str) -> int:
        """Mark tasks left RUNNING by an earlier TestBox process as abandoned."""
        cursor = self.connection.execute(
            "UPDATE task_history SET status = 'ABANDONED', finished_at = ?, error_code = 'HOST_INTERRUPTED' WHERE status = 'RUNNING'",
            (finished_at,),
        )
        self.connection.commit()
        return cursor.rowcount

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT id, plugin_name, plugin_version, command, params, started_at, finished_at, status, result_path, workspace_path, error_code FROM task_history WHERE id = ?", (task_id,)).fetchone()
        if not row: return None
        fields = ("id", "plugin_name", "plugin_version", "command", "params", "started_at", "finished_at", "status", "result_path", "workspace_path", "error_code")
        result = dict(zip(fields, row)); result["params"] = json.loads(result["params"])
        return result
