"""SQLite-backed task history repository with lightweight migrations."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from testbox.core.models import TaskStatus


def _is_process_alive(pid: int) -> bool:
    """Return whether a recorded Host PID still refers to a live process."""
    if pid <= 0:
        return False

    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not a portable liveness probe on Windows:
        # non-zero signals are implemented via TerminateProcess.  Query the
        # process exit code instead, without adding a third-party dependency.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        process = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            # Access denied still means that the PID exists.
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED
        try:
            exit_code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))) and exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(process)

    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


class TaskHistory:
    SCHEMA_VERSION = 1

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        self.connection.execute("CREATE TABLE IF NOT EXISTS schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = self.connection.execute("SELECT value FROM schema_metadata WHERE key = 'schema_version'").fetchone()
        current = int(row[0]) if row else 0
        if current < 1:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_history (
                    id TEXT PRIMARY KEY,
                    plugin_name TEXT NOT NULL,
                    plugin_version TEXT NOT NULL,
                    command TEXT NOT NULL,
                    params TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    result_path TEXT NOT NULL,
                    workspace_path TEXT NOT NULL,
                    error_code TEXT,
                    heartbeat_at TEXT,
                    host_pid INTEGER
                )
                """
            )
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_task_history_started_at ON task_history(started_at)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_task_history_status ON task_history(status)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS idx_task_history_command ON task_history(command)")
            self.connection.execute(
                "INSERT INTO schema_metadata(key, value) VALUES('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(self.SCHEMA_VERSION),),
            )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create(self, record: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO task_history
            (id, plugin_name, plugin_version, command, params, started_at, status,
             result_path, workspace_path, heartbeat_at, host_pid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record["plugin_name"],
                record["plugin_version"],
                record["command"],
                json.dumps(record["params"], ensure_ascii=False),
                record["started_at"],
                TaskStatus.PENDING.value,
                record["result_path"],
                record["workspace_path"],
                record.get("started_at"),
                record.get("host_pid"),
            ),
        )
        self.start(record["id"])

    def start(self, task_id: str) -> None:
        self.connection.execute(
            "UPDATE task_history SET status = ?, heartbeat_at = ? WHERE id = ? AND status = ?",
            (TaskStatus.RUNNING.value, self._now(), task_id, TaskStatus.PENDING.value),
        )
        self.connection.commit()

    def finish(self, task_id: str, *, status: str | TaskStatus, finished_at: str, error_code: str | None = None) -> None:
        value = status.value if isinstance(status, TaskStatus) else str(status)
        self.connection.execute(
            "UPDATE task_history SET status = ?, finished_at = ?, error_code = ?, heartbeat_at = ? WHERE id = ? AND status = ?",
            (value, finished_at, error_code, finished_at, task_id, TaskStatus.RUNNING.value),
        )
        self.connection.commit()

    def set_host_pid(self, task_id: str, host_pid: int) -> None:
        self.connection.execute(
            "UPDATE task_history SET host_pid = ? WHERE id = ? AND status = ?",
            (host_pid, task_id, TaskStatus.RUNNING.value),
        )
        self.connection.commit()

    def abandon_incomplete(self, finished_at: str) -> int:
        rows = self.connection.execute("SELECT id, host_pid FROM task_history WHERE status = ?", (TaskStatus.RUNNING.value,)).fetchall()
        abandoned: list[str] = []
        for row in rows:
            host_pid = row["host_pid"]
            if host_pid is None:
                abandoned.append(row["id"])
                continue
            if not _is_process_alive(host_pid):
                abandoned.append(row["id"])
        cursor = self.connection.executemany(
            "UPDATE task_history SET status = ?, finished_at = ?, error_code = 'HOST_INTERRUPTED' WHERE id = ? AND status = ?",
            [(TaskStatus.ABANDONED.value, finished_at, task_id, TaskStatus.RUNNING.value) for task_id in abandoned],
        )
        self.connection.commit()
        return cursor.rowcount

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM task_history WHERE id = ?", (task_id,)).fetchone()
        return self._to_dict(row) if row else None

    def list_tasks(self, *, status: str | TaskStatus | None = None, command: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value if isinstance(status, TaskStatus) else status)
        if command is not None:
            clauses.append("command = ?")
            values.append(command)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM task_history {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (*values, max(0, limit), max(0, offset)),
        ).fetchall()
        return [self._to_dict(row) for row in rows]

    def count(self, *, status: str | TaskStatus | None = None, command: str | None = None) -> int:
        clauses: list[str] = []
        values: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value if isinstance(status, TaskStatus) else status)
        if command is not None:
            clauses.append("command = ?")
            values.append(command)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return int(self.connection.execute(f"SELECT COUNT(*) FROM task_history {where}", values).fetchone()[0])

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["params"] = json.loads(result["params"])
        return result

    @staticmethod
    def _now() -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()
