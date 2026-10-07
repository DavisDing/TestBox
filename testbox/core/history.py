"""SQLite-backed task history repository with lightweight migrations."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

from testbox.core.models import TaskStatus


def _is_process_alive(pid: int) -> bool:
    """Return whether a recorded Host or owner PID refers to a live process."""
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
    except PermissionError:
        # A process owned by another user may exist but reject the probe.
        return True
    except OSError:
        return False
    return True


class TaskHistory:
    SCHEMA_VERSION = 2
    TERMINAL_STATUSES = (
        TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value, TaskStatus.ABANDONED.value,
    )

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        try:
            self._migrate()
        except Exception:
            self.connection.close()
            raise

    def _migrate(self) -> None:
        # Serialize version reads and ALTER TABLE across concurrent startups.
        # The connection's default busy timeout bounds waiting for other writers.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("CREATE TABLE IF NOT EXISTS schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = self.connection.execute("SELECT value FROM schema_metadata WHERE key = 'schema_version'").fetchone()
            current = int(row[0]) if row else 0
            if current > self.SCHEMA_VERSION:
                raise ValueError(
                    f"Unsupported task history schema version {current}; maximum supported is {self.SCHEMA_VERSION}"
                )
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
            if current < 2:
                columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(task_history)")}
                if "owner_pid" not in columns:
                    self.connection.execute("ALTER TABLE task_history ADD COLUMN owner_pid INTEGER")
                self.connection.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(self.SCHEMA_VERSION),),
                )

    def close(self) -> None:
        self.connection.close()

    def create(self, record: dict[str, Any]) -> None:
        # Creation and the initial RUNNING transition are one atomic write.
        # owner_pid is explicit: legacy/manual records must remain unowned.
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO task_history
                (id, plugin_name, plugin_version, command, params, started_at, status,
                 result_path, workspace_path, heartbeat_at, host_pid, owner_pid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["plugin_name"],
                    record["plugin_version"],
                    record["command"],
                    json.dumps(record["params"], ensure_ascii=False),
                    record["started_at"],
                    TaskStatus.RUNNING.value,
                    record["result_path"],
                    record["workspace_path"],
                    self._now(),
                    record.get("host_pid"),
                    record.get("owner_pid"),
                ),
            )

    def start(self, task_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE task_history SET status = ?, heartbeat_at = ? WHERE id = ? AND status = ?",
                (TaskStatus.RUNNING.value, self._now(), task_id, TaskStatus.PENDING.value),
            )

    def finish(self, task_id: str, *, status: str | TaskStatus, finished_at: str, error_code: str | None = None) -> None:
        value = status.value if isinstance(status, TaskStatus) else str(status)
        with self.connection:
            self.connection.execute(
                "UPDATE task_history SET status = ?, finished_at = ?, error_code = ?, heartbeat_at = ? WHERE id = ? AND status = ?",
                (value, finished_at, error_code, finished_at, task_id, TaskStatus.RUNNING.value),
            )

    def set_host_pid(self, task_id: str, host_pid: int) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE task_history SET host_pid = ? WHERE id = ? AND status = ?",
                (host_pid, task_id, TaskStatus.RUNNING.value),
            )

    def abandon_incomplete(
        self, finished_at: str, *, is_task_active: Callable[[str], bool] | None = None,
    ) -> int:
        # Lock before the snapshot, not after probing: another connection must
        # not record a Host PID between our liveness check and terminal update.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            rows = self.connection.execute(
                "SELECT id, host_pid, owner_pid FROM task_history WHERE status IN (?, ?)",
                (TaskStatus.PENDING.value, TaskStatus.RUNNING.value),
            ).fetchall()
            abandoned: list[str] = []
            for row in rows:
                pid = row["host_pid"] if row["host_pid"] is not None else row["owner_pid"]
                if pid is None or not _is_process_alive(pid):
                    # The Host may have just exited while its live Runtime is
                    # still persisting the result. A nonblocking lifecycle-lock
                    # probe distinguishes this from an interrupted owner.
                    if is_task_active is None or not is_task_active(row["id"]):
                        abandoned.append(row["id"])
            cursor = self.connection.executemany(
                "UPDATE task_history SET status = ?, finished_at = ?, error_code = 'HOST_INTERRUPTED' WHERE id = ? AND status IN (?, ?)",
                [(TaskStatus.ABANDONED.value, finished_at, task_id, TaskStatus.PENDING.value, TaskStatus.RUNNING.value) for task_id in abandoned],
            )
            return cursor.rowcount

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM task_history WHERE id = ?", (task_id,)).fetchone()
        return self._to_dict(row) if row else None

    @staticmethod
    def _task_filters(
        *,
        status: str | TaskStatus | None = None,
        command: str | None = None,
        task_id_query: str | None = None,
        started_from: str | None = None,
        started_before: str | None = None,
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value if isinstance(status, TaskStatus) else status)
        if command is not None:
            clauses.append("command = ?")
            values.append(command)
        if task_id_query:
            escaped = task_id_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("id LIKE ? ESCAPE '\\'")
            values.append(f"%{escaped}%")
        if started_from is not None:
            clauses.append("started_at >= ?")
            values.append(started_from)
        if started_before is not None:
            clauses.append("started_at < ?")
            values.append(started_before)
        return clauses, values

    def list_tasks(
        self,
        *,
        status: str | TaskStatus | None = None,
        command: str | None = None,
        task_id_query: str | None = None,
        started_from: str | None = None,
        started_before: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses, values = self._task_filters(
            status=status,
            command=command,
            task_id_query=task_id_query,
            started_from=started_from,
            started_before=started_before,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM task_history {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (*values, max(0, limit), max(0, offset)),
        ).fetchall()
        return [self._to_dict(row) for row in rows]

    def count(
        self,
        *,
        status: str | TaskStatus | None = None,
        command: str | None = None,
        task_id_query: str | None = None,
        started_from: str | None = None,
        started_before: str | None = None,
    ) -> int:
        clauses, values = self._task_filters(
            status=status,
            command=command,
            task_id_query=task_id_query,
            started_from=started_from,
            started_before=started_before,
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return int(self.connection.execute(f"SELECT COUNT(*) FROM task_history {where}", values).fetchone()[0])


    def clean_before(self, before_timestamp: str) -> int:
        """Delete only known terminal tasks older than the ISO boundary."""
        placeholders = ", ".join("?" for _ in self.TERMINAL_STATUSES)
        with self.connection:
            cursor = self.connection.execute(
                f"DELETE FROM task_history WHERE started_at < ? AND status IN ({placeholders})",
                (before_timestamp, *self.TERMINAL_STATUSES),
            )
            return cursor.rowcount

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["params"] = json.loads(result["params"])
        return result

    @staticmethod
    def _now() -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()
