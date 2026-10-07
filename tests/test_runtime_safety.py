"""Runtime regression tests for lifecycle, cleanup and diagnostic isolation."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from testbox.core.locks import PluginExecutionLock
from testbox.core.process_runner import HostExecution
from testbox.core.runtime import Runtime


class RuntimeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        plugin = self.root / "plugins" / "audit-tool"
        (plugin / "src").mkdir(parents=True)
        (plugin / "schemas").mkdir()
        (plugin / "manifest.yaml").write_text('''schema_version: 1
name: audit-tool
version: 1.0.0
description: Runtime safety test fixture
category: test
core_compatibility: ">=1.0.0,<2.0.0"
entry: src.main:Plugin
commands:
  - name: audit.run
    description: Test execution
    input_schema: schemas/run.json
capabilities:
  concurrency: true
  network: false
  filesystem: output-only
  resources: []
''', encoding="utf-8")
        (plugin / "schemas" / "run.json").write_text(json.dumps({
            "type": "object", "properties": {
                "api_key": {"type": "string"}, "mode": {"type": "string"},
            },
        }), encoding="utf-8")
        (plugin / "src" / "main.py").write_text('''from testbox.sdk import Result
class Plugin:
    def init(self, context): self.context = context
    def execute(self, command, params):
        if params.get("mode") == "error":
            self.context.logger.info(params["api_key"])
            raise ValueError(params["api_key"])
        path = self.context.files.write_text("result.txt", "TEST DATA ONLY")
        return Result("success", "done", files=[path])
    def destroy(self): pass
''', encoding="utf-8")
        self.runtime = Runtime(self.root)

    def tearDown(self):
        self.runtime.close()
        self.temporary.cleanup()

    def test_invalid_config_never_creates_a_running_task(self):
        (self.root / "config.yaml").write_text("[broken", encoding="utf-8")
        before = set(self.runtime.workspace_dir.iterdir())
        with self.assertRaises(ValueError):
            self.runtime.run("audit.run", {})
        self.assertEqual(self.runtime.count_tasks(), 0)
        self.assertEqual(set(self.runtime.workspace_dir.iterdir()), before)

    def test_invalid_config_without_pyyaml_never_creates_task(self):
        (self.root / "config.yaml").write_text("[broken", encoding="utf-8")
        with patch("testbox.core.manifest.yaml", None):
            with self.assertRaises(ValueError):
                self.runtime.run("audit.run", {})
        self.assertEqual(self.runtime.count_tasks(), 0)
        self.assertFalse(any(
            child.name.startswith("20") for child in self.runtime.workspace_dir.iterdir()
        ))

    def test_peer_runtime_before_host_spawn_does_not_abandon_task(self):
        owner = self

        class Runner:
            def run(self, request, *, task_id, on_started):
                peer = Runtime(owner.root)
                try:
                    record = peer.get_task(task_id)
                    owner.assertEqual(record["status"], "RUNNING")
                    owner.assertEqual(record["owner_pid"], os.getpid())
                    owner.assertIsNone(record["host_pid"])
                finally:
                    peer.close()
                on_started(os.getpid())
                return HostExecution({"status": "success", "message": "ok"}, 0, "", os.getpid())

        self.runtime.process_runner = Runner()
        task_id, result = self.runtime.run("audit.run", {})
        self.assertEqual(result.status, "success")
        self.assertEqual(self.runtime.get_task(task_id)["status"], "SUCCEEDED")

    def test_peer_after_host_exit_does_not_abandon_result_persistence(self):
        finish = self.runtime.history.finish

        def finish_after_peer(task_id, **kwargs):
            # The real Host has exited, but the Runtime still owns the task.
            peer = Runtime(self.root)
            try:
                self.assertEqual(peer.get_task(task_id)["status"], "RUNNING")
            finally:
                peer.close()
            return finish(task_id, **kwargs)

        with patch.object(self.runtime.history, "finish", side_effect=finish_after_peer):
            task_id, result = self.runtime.run("audit.run", {})
        self.assertEqual(result.status, "success")
        self.assertEqual(self.runtime.get_task(task_id)["status"], "SUCCEEDED")

    def test_cleanup_skips_task_before_history_creation(self):
        stage = self.runtime.workspace.stage_file_inputs

        def clean_during_stage(schema, params, paths):
            peer = Runtime(self.root)
            try:
                self.assertEqual(peer.clean_workspace(date.today() + timedelta(days=1)), 0)
                self.assertTrue(paths.root.is_dir())
            finally:
                peer.close()
            return stage(schema, params, paths)

        with patch.object(self.runtime.workspace, "stage_file_inputs", side_effect=clean_during_stage):
            task_id, result = self.runtime.run("audit.run", {})
        self.assertEqual(result.status, "success")
        self.assertTrue((self.runtime.workspace_dir / task_id / "output" / "result.txt").is_file())

    def test_cleanup_skips_running_task_then_cleans_terminal_task(self):
        runner = self.runtime.process_runner.run

        def run_and_clean(request, *, task_id, on_started):
            peer = Runtime(self.root)
            try:
                self.assertEqual(peer.get_task(task_id)["status"], "RUNNING")
                self.assertEqual(peer.clean_workspace(date.today() + timedelta(days=1)), 0)
                self.assertEqual(peer.clean_history(date.today() + timedelta(days=1)), 0)
            finally:
                peer.close()
            return runner(request, task_id=task_id, on_started=on_started)

        with patch.object(self.runtime.process_runner, "run", side_effect=run_and_clean):
            task_id, result = self.runtime.run("audit.run", {})
        self.assertEqual(result.status, "success")
        self.assertEqual(self.runtime.clean_workspace(date.today() + timedelta(days=1)), 1)
        self.assertEqual(self.runtime.clean_history(date.today() + timedelta(days=1)), 1)
        self.assertIsNone(self.runtime.get_task(task_id))

    def test_history_write_retry_keeps_original_success_and_files(self):
        finish = self.runtime.history.finish
        with patch.object(self.runtime.history, "finish") as mocked:
            # The second attempt delegates to the real store, not a fabricated success.
            def finish_once(*args, **kwargs):
                if mocked.call_count == 1:
                    raise sqlite3.OperationalError("locked")
                return finish(*args, **kwargs)
            mocked.side_effect = finish_once
            task_id, result = self.runtime.run("audit.run", {})
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.files, ["result.txt"])
        self.assertEqual(self.runtime.get_task(task_id)["status"], "SUCCEEDED")
        self.assertIn("重试后已恢复", " ".join(result.warnings))
        self.assertEqual(self.runtime.get_task_result(task_id)["files"], ["result.txt"])

    def test_persistent_history_failure_preserves_original_result(self):
        with patch.object(self.runtime.history, "finish", side_effect=sqlite3.OperationalError("locked")):
            task_id, result = self.runtime.run("audit.run", {})
        self.assertEqual(result.status, "success")
        self.assertEqual(result.files, ["result.txt"])
        self.assertIn("任务历史同步失败", " ".join(result.warnings))
        self.assertEqual(self.runtime.get_task_result(task_id)["status"], "success")
        self.assertTrue(self.runtime.get_task_output_path(task_id, "result.txt").is_file())
        self.assertEqual(self.runtime.clean_workspace(date.today() + timedelta(days=1)), 0)

    def test_host_exception_never_persists_secret(self):
        secret = "SYNTHETIC_AUDIT_SECRET_NOT_REAL"
        task_id, result = self.runtime.run("audit.run", {"api_key": secret, "mode": "error"})
        self.assertEqual(result.status, "failed")
        for path in (self.runtime.workspace_dir / task_id).rglob("*"):
            if path.is_file():
                self.assertNotIn(secret, path.read_text(encoding="utf-8"), str(path))
        self.assertNotIn(secret, json.dumps(self.runtime.get_task(task_id)))

    def test_runtime_redacts_noncompliant_host_stderr_and_payload(self):
        secret = "SYNTHETIC_AUDIT_SECRET_NOT_REAL"

        class Runner:
            def run(self, request, *, task_id, on_started):
                on_started(os.getpid())
                return HostExecution({
                    "status": "failed", "message": secret,
                    "data": {"diagnostic": secret, "error_code": "TEST"},
                    "files": [], "warnings": [secret],
                }, 1, secret, os.getpid())

        self.runtime.process_runner = Runner()
        task_id, result = self.runtime.run("audit.run", {"api_key": secret})
        self.assertNotIn(secret, json.dumps(result.to_dict()))
        self.assertNotIn(secret, (self.runtime.workspace_dir / task_id / "report.md").read_text())

    def test_secret_matching_status_or_path_does_not_break_machine_identity(self):
        for secret in ("success", "message", "result.txt"):
            with self.subTest(secret=secret):
                task_id, result = self.runtime.run("audit.run", {"api_key": secret})
                self.assertEqual(result.status, "success")
                self.assertEqual(self.runtime.get_task(task_id)["status"], "SUCCEEDED")
                self.assertEqual(result.files, ["result.txt"])
                self.assertTrue(self.runtime.get_task_output_path(task_id, "result.txt").is_file())
                self.assertEqual(self.runtime.get_task_result(task_id)["status"], "success")

    def test_log_tail_reads_bounded_unicode_and_zero_length(self):
        task_id, _ = self.runtime.run("audit.run", {})
        log = self.runtime.workspace_dir / task_id / "logs" / "task.log"
        log.write_text("前缀" * 10_000 + "最终日志😀", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=AssertionError("must not read entire log")):
            self.assertEqual(self.runtime.get_task_log(task_id, max_chars=5), "最终日志😀")
            self.assertEqual(self.runtime.get_task_log(task_id, max_chars=0), "")


class TaskLockTests(unittest.TestCase):
    def test_nonblocking_acquire_skips_busy_lock_then_succeeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = PluginExecutionLock(Path(temporary) / "task.lock")
            second = PluginExecutionLock(Path(temporary) / "task.lock")
            first.acquire()
            try:
                self.assertFalse(second.try_acquire())
            finally:
                first.release()
            self.assertTrue(second.try_acquire())
            second.release()


if __name__ == "__main__":
    unittest.main()
