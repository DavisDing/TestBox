"""Task history migration, recovery, cleanup, and write safety regressions."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from testbox.core.history import TaskHistory, _is_process_alive
from testbox.core.models import TaskStatus


STARTED = "2020-01-01T00:00:00+00:00"
FINISHED = "2020-01-01T01:00:00+00:00"
BEFORE = "2020-01-02T00:00:00+00:00"


@contextmanager
def database(path):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            yield connection


class HistorySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.sqlite3"
        self.history = self.open_history()

    def open_history(self):
        history = TaskHistory(self.path)
        self.addCleanup(history.close)
        return history

    def create(self, task_id="task", **extra):
        record = {
            "id": task_id,
            "plugin_name": "example",
            "plugin_version": "1.0.0",
            "command": "example.run",
            "params": {"label": "测试"},
            "started_at": STARTED,
            "result_path": "workspace/result.json",
            "workspace_path": "workspace",
            **extra,
        }
        self.history.create(record)
        return self.history.get(task_id)

    def set_status(self, task_id, status):
        with self.history.connection:
            self.history.connection.execute(
                "UPDATE task_history SET status = ? WHERE id = ?", (status, task_id)
            )

    def test_new_database_has_version_two_and_nullable_owner(self):
        self.assertEqual(TaskHistory.SCHEMA_VERSION, 2)
        version = self.history.connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        self.assertEqual(version, "2")
        columns = {row["name"]: row for row in self.history.connection.execute("PRAGMA table_info(task_history)")}
        self.assertEqual(columns["owner_pid"]["type"], "INTEGER")
        self.assertEqual(columns["owner_pid"]["notnull"], 0)
        self.assertIsNone(self.create()["owner_pid"])
        self.assertEqual(self.create("owned", owner_pid=os.getpid())["owner_pid"], os.getpid())

    def test_live_owner_before_spawn_survives_second_connection(self):
        self.create(owner_pid=os.getpid())
        other = self.open_history()
        self.assertEqual(other.abandon_incomplete(FINISHED), 0)
        self.assertEqual(other.get("task")["status"], TaskStatus.RUNNING.value)
        self.assertIsNone(other.get("task")["host_pid"])
        self.history.set_host_pid("task", os.getpid())
        self.history.finish("task", status=TaskStatus.SUCCEEDED, finished_at=FINISHED)
        self.assertEqual(other.get("task")["status"], TaskStatus.SUCCEEDED.value)

    def test_pending_task_with_live_owner_survives_recovery(self):
        self.create(owner_pid=os.getpid())
        self.set_status("task", TaskStatus.PENDING.value)
        self.assertEqual(self.open_history().abandon_incomplete(FINISHED), 0)
        self.assertEqual(self.history.get("task")["status"], TaskStatus.PENDING.value)

    def test_dead_owner_is_abandoned(self):
        self.create(owner_pid=12345)
        with patch("testbox.core.history._is_process_alive", return_value=False) as alive:
            self.assertEqual(self.open_history().abandon_incomplete(FINISHED), 1)
        alive.assert_called_once_with(12345)
        task = self.history.get("task")
        self.assertEqual(task["status"], TaskStatus.ABANDONED.value)
        self.assertEqual(task["finished_at"], FINISHED)
        self.assertEqual(task["error_code"], "HOST_INTERRUPTED")
        self.assertEqual(self.history.abandon_incomplete(BEFORE), 0)
        self.assertEqual(self.history.get("task"), task)

    def test_unowned_pending_and_running_tasks_are_abandoned(self):
        self.create("running")
        self.create("pending")
        self.set_status("pending", TaskStatus.PENDING.value)
        with patch("testbox.core.history._is_process_alive") as alive:
            self.assertEqual(self.history.abandon_incomplete(FINISHED), 2)
        alive.assert_not_called()
        for task_id in ("pending", "running"):
            self.assertEqual(self.history.get(task_id)["status"], TaskStatus.ABANDONED.value)

    def test_live_host_takes_precedence_over_dead_owner(self):
        self.create(owner_pid=12345, host_pid=os.getpid())
        with patch("testbox.core.history._is_process_alive", return_value=True) as alive:
            self.assertEqual(self.open_history().abandon_incomplete(FINISHED), 0)
        alive.assert_called_once_with(os.getpid())
        self.assertEqual(self.history.get("task")["status"], TaskStatus.RUNNING.value)

    def test_live_host_without_owner_survives_recovery(self):
        self.create(host_pid=os.getpid())
        self.assertEqual(self.open_history().abandon_incomplete(FINISHED), 0)
        self.assertEqual(self.history.get("task")["status"], TaskStatus.RUNNING.value)

    def test_dead_host_takes_precedence_over_live_owner(self):
        self.create(owner_pid=os.getpid(), host_pid=12345)
        with patch("testbox.core.history._is_process_alive", return_value=False) as alive:
            self.assertEqual(self.history.abandon_incomplete(FINISHED), 1)
        alive.assert_called_once_with(12345)
        self.assertEqual(self.history.get("task")["status"], TaskStatus.ABANDONED.value)

    def test_cleanup_preserves_active_and_unknown_states(self):
        for task_id, status in (("pending", "PENDING"), ("running", "RUNNING"), ("future", "QUEUED")):
            self.create(task_id)
            self.set_status(task_id, status)
        self.assertEqual(self.history.clean_before(BEFORE), 0)
        self.assertEqual(self.history.count(), 3)

    def test_cleanup_deletes_only_explicit_old_terminal_states(self):
        for status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.ABANDONED):
            self.create(status.value)
            self.history.finish(status.value, status=status, finished_at=FINISHED)
        self.create("boundary", started_at=BEFORE)
        self.history.finish("boundary", status=TaskStatus.SUCCEEDED, finished_at=BEFORE)
        self.assertEqual(self.history.clean_before(BEFORE), 4)
        self.assertEqual(self.history.count(), 1)
        self.assertIsNotNone(self.history.get("boundary"))
        self.assertEqual(self.history.clean_before(BEFORE), 0)

    def test_duplicate_finish_and_late_host_cannot_rewrite_terminal_tasks(self):
        for status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.ABANDONED):
            with self.subTest(status=status):
                self.create(status.value)
                self.history.finish(status.value, status=status, finished_at=FINISHED, error_code="original")
                original = self.history.get(status.value)
                self.open_history().finish(status.value, status=TaskStatus.FAILED, finished_at=BEFORE, error_code="late")
                self.history.set_host_pid(status.value, os.getpid())
                self.history.start(status.value)
                self.assertEqual(self.history.get(status.value), original)
        self.assertEqual(self.history.abandon_incomplete(BEFORE), 0)

    def test_recovery_failure_rolls_back_all_updates_and_releases_lock(self):
        self.create("first")
        self.create("second")
        with self.history.connection:
            self.history.connection.execute("""
                CREATE TRIGGER reject_recovery BEFORE UPDATE ON task_history
                WHEN NEW.status = 'ABANDONED' AND OLD.id = 'second'
                BEGIN SELECT RAISE(ABORT, 'injected recovery failure'); END
            """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected recovery failure"):
            self.history.abandon_incomplete(FINISHED)
        self.assertFalse(self.history.connection.in_transaction)
        for task_id in ("first", "second"):
            self.assertEqual(self.history.get(task_id)["status"], TaskStatus.RUNNING.value)
        other = self.open_history()
        other.finish("first", status=TaskStatus.SUCCEEDED, finished_at=FINISHED)
        self.assertEqual(self.history.get("first")["status"], TaskStatus.SUCCEEDED.value)

    def test_failed_create_does_not_leave_a_transaction_or_partial_record(self):
        original = self.create()
        with self.assertRaises(sqlite3.IntegrityError):
            self.create(owner_pid=os.getpid())
        self.assertFalse(self.history.connection.in_transaction)
        self.assertEqual(self.history.get("task"), original)
        other = self.open_history()
        other.finish("task", status=TaskStatus.SUCCEEDED, finished_at=FINISHED)
        self.assertEqual(self.history.get("task")["status"], TaskStatus.SUCCEEDED.value)

    def test_failed_finish_keeps_task_retriable_and_releases_lock(self):
        self.create()
        with self.history.connection:
            self.history.connection.execute("""
                CREATE TRIGGER reject_finish BEFORE UPDATE ON task_history
                WHEN NEW.status = 'SUCCEEDED'
                BEGIN SELECT RAISE(ABORT, 'injected finish failure'); END
            """)
        original = self.history.get("task")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected finish failure"):
            self.history.finish("task", status=TaskStatus.SUCCEEDED, finished_at=FINISHED)
        self.assertFalse(self.history.connection.in_transaction)
        self.assertEqual(self.history.get("task"), original)
        other = self.open_history()
        with other.connection:
            other.connection.execute("DROP TRIGGER reject_finish")
        other.finish("task", status=TaskStatus.SUCCEEDED, finished_at=FINISHED)
        self.assertEqual(self.history.get("task")["status"], TaskStatus.SUCCEEDED.value)

    def test_cleanup_failure_rolls_back_and_releases_lock(self):
        for task_id in ("first", "second"):
            self.create(task_id)
            self.history.finish(task_id, status=TaskStatus.SUCCEEDED, finished_at=FINISHED)
        with self.history.connection:
            self.history.connection.execute("""
                CREATE TRIGGER reject_cleanup BEFORE DELETE ON task_history
                WHEN OLD.id = 'second'
                BEGIN SELECT RAISE(ABORT, 'injected cleanup failure'); END
            """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected cleanup failure"):
            self.history.clean_before(BEFORE)
        self.assertFalse(self.history.connection.in_transaction)
        self.assertEqual(self.history.count(), 2)
        with self.open_history().connection as connection:
            connection.execute("DROP TRIGGER reject_cleanup")
        self.assertEqual(self.history.clean_before(BEFORE), 2)

    def test_recovery_blocks_host_registration_until_liveness_snapshot_finishes(self):
        self.create(owner_pid=os.getpid())
        probed = threading.Event()
        writer_started = threading.Event()
        writer_done = threading.Event()
        errors = []

        def register_host():
            connection = None
            try:
                probed.wait(timeout=2)
                connection = sqlite3.connect(self.path)
                writer_started.set()
                with connection:
                    connection.execute("UPDATE task_history SET host_pid = ? WHERE id = 'task'", (os.getpid(),))
                writer_done.set()
            except Exception as error:
                errors.append(error)
            finally:
                if connection is not None:
                    connection.close()

        def check_alive(pid):
            probed.set()
            self.assertTrue(writer_started.wait(timeout=2))
            self.assertFalse(writer_done.wait(timeout=0.05))
            return True

        writer = threading.Thread(target=register_host)
        writer.start()
        try:
            with patch("testbox.core.history._is_process_alive", side_effect=check_alive):
                self.assertEqual(self.history.abandon_incomplete(FINISHED), 0)
        finally:
            probed.set()
            writer.join(timeout=3)
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(writer_done.is_set())
        self.assertEqual(self.history.get("task")["host_pid"], os.getpid())

    @unittest.skipIf(os.name == "nt", "POSIX liveness permission handling")
    def test_permission_denied_probe_means_process_is_alive(self):
        with patch("testbox.core.history.os.kill", side_effect=PermissionError):
            self.assertTrue(_is_process_alive(12345))
        with patch("testbox.core.history.os.kill", side_effect=ProcessLookupError):
            self.assertFalse(_is_process_alive(12345))
        self.assertFalse(_is_process_alive(0))
        self.assertFalse(_is_process_alive(-1))


class HistoryMigrationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.sqlite3"

    def make_legacy_database(self, metadata=True, version="1"):
        with database(self.path) as connection:
            connection.executescript("""
                CREATE TABLE task_history (
                    id TEXT PRIMARY KEY, plugin_name TEXT NOT NULL,
                    plugin_version TEXT NOT NULL, command TEXT NOT NULL,
                    params TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                    status TEXT NOT NULL, result_path TEXT NOT NULL, workspace_path TEXT NOT NULL,
                    error_code TEXT, heartbeat_at TEXT, host_pid INTEGER
                );
                CREATE INDEX idx_task_history_started_at ON task_history(started_at);
                CREATE INDEX idx_task_history_status ON task_history(status);
                CREATE INDEX idx_task_history_command ON task_history(command);
            """)
            if metadata:
                connection.execute("CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                connection.execute("INSERT INTO schema_metadata VALUES ('schema_version', ?)", (version,))
            connection.execute("""
                INSERT INTO task_history VALUES (
                    'old', 'example', '1.0.0', 'example.run', '{"label": "测试"}', ?, ?,
                    'SUCCEEDED', 'old/result.json', 'old', NULL, ?, NULL
                )
            """, (STARTED, FINISHED, FINISHED))

    def open_history(self):
        history = TaskHistory(self.path)
        self.addCleanup(history.close)
        return history

    def test_version_one_migrates_without_changing_existing_data_or_indexes(self):
        self.make_legacy_database()
        with database(self.path) as connection:
            original = connection.execute("SELECT * FROM task_history").fetchone()
            indexes = connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' ORDER BY name").fetchall()
        history = self.open_history()
        migrated = history.connection.execute("SELECT * FROM task_history").fetchone()
        self.assertEqual(tuple(migrated)[:-1], original)
        self.assertIsNone(migrated["owner_pid"])
        self.assertEqual(history.get("old")["params"], {"label": "测试"})
        self.assertEqual(history.connection.execute("SELECT value FROM schema_metadata WHERE key = 'schema_version'").fetchone()[0], "2")
        actual_indexes = history.connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'index' ORDER BY name").fetchall()
        self.assertEqual([tuple(row) for row in actual_indexes], indexes)
        self.assertEqual(self.open_history().get("old"), history.get("old"))

    def test_unversioned_legacy_database_migrates(self):
        self.make_legacy_database(metadata=False)
        history = self.open_history()
        self.assertIsNone(history.get("old")["owner_pid"])
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.connection.execute("SELECT value FROM schema_metadata WHERE key = 'schema_version'").fetchone()[0], "2")

    def test_future_version_is_rejected_without_changing_database(self):
        self.make_legacy_database(version="3")
        with database(self.path) as connection:
            before = list(connection.iterdump())
        with self.assertRaisesRegex(ValueError, "Unsupported task history schema version 3"):
            TaskHistory(self.path)
        with database(self.path) as connection:
            self.assertEqual(list(connection.iterdump()), before)
            # The rejected constructor must not retain a database write lock.
            connection.execute("UPDATE schema_metadata SET value = '1' WHERE key = 'schema_version'")
        self.assertEqual(self.open_history().count(), 1)

    def test_failed_migration_rolls_back_column_and_version_together(self):
        self.make_legacy_database()
        with database(self.path) as connection:
            connection.execute("""
                CREATE TRIGGER reject_version BEFORE UPDATE ON schema_metadata
                BEGIN SELECT RAISE(ABORT, 'injected migration failure'); END
            """)
            before = list(connection.iterdump())
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected migration failure"):
            TaskHistory(self.path)
        with database(self.path) as connection:
            self.assertEqual(list(connection.iterdump()), before)
            connection.execute("DROP TRIGGER reject_version")
        self.assertEqual(self.open_history().count(), 1)

    def test_concurrent_migrations_are_serialized_and_idempotent(self):
        self.make_legacy_database()
        barrier = threading.Barrier(3)
        errors = []

        def migrate():
            history = None
            try:
                barrier.wait(timeout=3)
                history = TaskHistory(self.path)
                self.assertEqual(history.count(), 1)
            except Exception as error:
                errors.append(error)
            finally:
                if history is not None:
                    history.close()

        threads = [threading.Thread(target=migrate) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=3)
        for thread in threads:
            thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        history = self.open_history()
        names = [row["name"] for row in history.connection.execute("PRAGMA table_info(task_history)")]
        self.assertEqual(names.count("owner_pid"), 1)
        self.assertEqual(history.count(), 1)


if __name__ == "__main__":
    unittest.main()
