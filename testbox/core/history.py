"""SQLite-backed task history with terminal-state protection."""
from __future__ import annotations

import json
import os
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

    def set_host_pid(self, task_id: str, host_pid: int) -> None:
        self.connection.execute("UPDATE task_history SET host_pid = ? WHERE id = ? AND status = 'RUNNING'", (host_pid, task_id))
        self.connection.commit()

    def abandon_incomplete(self, finished_at: str) -> int:
        """Mark only RUNNING tasks whose recorded Host process no longer exists."""
        rows = self.connection.execute("SELECT id, host_pid FROM task_history WHERE status = 'RUNNING'").fetchall()
        abandoned = []
        for task_id, host_pid in rows:
            if host_pid is None:
                abandoned.append(task_id)
                continue
            try:
                os.kill(host_pid, 0)
            except OSError:
                abandoned.append(task_id)
        cursor = self.connection.executemany(
            "UPDATE task_history SET status = 'ABANDONED', finished_at = ?, error_code = 'HOST_INTERRUPTED' WHERE id = ? AND status = 'RUNNING'",
            [(finished_at, task_id) for task_id in abandoned],
        )
        self.connection.commit()
        return cursor.rowcount

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT id, plugin_name, plugin_version, command, params, started_at, finished_at, status, result_path, workspace_path, error_code FROM task_history WHERE id = ?", (task_id,)).fetchone()
        if not row: return None
        fields = ("id", "plugin_name", "plugin_version", "command", "params", "started_at", "finished_at", "status", "result_path", "workspace_path", "error_code")
        result = dict(zip(fields, row)); result["params"] = json.loads(result["params"])
        return result
