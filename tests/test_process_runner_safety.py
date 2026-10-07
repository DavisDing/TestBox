from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from testbox.core.errors import ErrorCode
from testbox.core.process_runner import ProcessRunner


class ProcessRunnerSafetyTests(unittest.TestCase):
    EVENT = {
        "protocol_version": 1, "event": "result", "task_id": "task-1",
        "result": {"status": "success", "message": "ok", "data": {}, "files": [], "warnings": []},
    }

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.python = sys.executable
        self.real_popen = subprocess.Popen
        self.processes = []
        self.commands = []
        self.runner = ProcessRunner(self.root, timeout_seconds=3)
        self.runner.MAX_RESPONSE_BYTES = 4096
        self.runner.MAX_STDERR_BYTES = 1024

    def _launch(self, script, *, frozen_gui=False, request=None, on_started=None):
        def launch(command, **kwargs):
            self.commands.append(command)
            # Run actual OS child processes while substituting the executable
            # entry point: frozen GUI packaging is not available on this host.
            args = command[2:] if "--response-file" in command else []
            process = self.real_popen([self.python, "-u", "-c", script, *args], **kwargs)
            self.processes.append(process)
            self.addCleanup(self._emergency_reap, process)
            return process

        with ExitStack() as stack:
            stack.enter_context(patch("testbox.core.process_runner.subprocess.Popen", side_effect=launch))
            if frozen_gui:
                stack.enter_context(patch.object(sys, "frozen", True, create=True))
                stack.enter_context(patch.object(sys, "executable", str(self.root / "TestBox-GUI.exe")))
            return self.runner.run(request or {}, task_id="task-1", on_started=on_started)

    @staticmethod
    def _emergency_reap(process):
        # Even a failing safety assertion must not leave its test child alive.
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()

    def _assert_reaped(self, *, killed=False, gui=False):
        self.assertEqual(len(self.processes), 1)
        process = self.processes[0]
        self.assertIsNotNone(process.poll())
        self.assertIsNotNone(process.wait(timeout=1))
        if killed:
            self.assertNotEqual(process.returncode, 0)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                self.assertTrue(pipe.closed)
        if gui:
            command = self.commands[0]
            request_path = Path(command[command.index("--request-file") + 1])
            self.assertFalse(request_path.parent.exists())

    def _success_script(self):
        return f"import sys; sys.stdin.read(); sys.stdout.write({json.dumps(self.EVENT)!r})"

    def test_normal_single_response(self):
        result = self._launch(self._success_script(), request={"hello": "世界"})
        self.assertEqual(result.payload, self.EVENT["result"])
        self.assertEqual(result.stderr, "")
        self._assert_reaped()

    def test_overflow_found_by_reader_after_process_exit_is_rejected(self):
        real_read = os.read
        delayed_chunks = []
        with ExitStack() as stack:
            def delay_until_host_exit(pid):
                process = self.processes[0]
                stdout_fd = process.stdout.fileno()

                def delayed_read(fd, size):
                    chunk = real_read(fd, size)
                    if fd == stdout_fd and chunk:
                        process.wait(timeout=2)
                        time.sleep(0.05)
                        delayed_chunks.append(len(chunk))
                    return chunk

                stack.enter_context(patch("testbox.core.process_runner.os.read", side_effect=delayed_read))

            result = self._launch("import os; os.write(1, b'x' * 5000)", on_started=delay_until_host_exit)
        self.assertTrue(delayed_chunks)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.OUTPUT_TOO_LARGE)
        self._assert_reaped()

    def test_kill_oserror_does_not_mask_original_callback_error(self):
        error = RuntimeError("original callback failure")
        with ExitStack() as stack:
            def callback(pid):
                process = self.processes[0]
                real_kill = process.kill

                def kill_then_error():
                    real_kill()
                    raise PermissionError("kill reported failure")

                stack.enter_context(patch.object(process, "kill", side_effect=kill_then_error))
                wait = stack.enter_context(patch.object(process, "wait", wraps=process.wait))
                self.wait_calls = wait
                raise error

            with self.assertRaises(RuntimeError) as caught:
                self._launch("import time; time.sleep(30)", frozen_gui=True, on_started=callback)
        self.assertIs(caught.exception, error)
        self.assertTrue(self.wait_calls.called)
        self.assertIn("PermissionError", " ".join(error.__notes__))
        self._assert_reaped(killed=True, gui=True)

    def test_failed_kill_and_wait_preserve_callback_error_and_close_resources(self):
        error = ValueError("original callback failure")
        with ExitStack() as stack:
            def callback(pid):
                process = self.processes[0]
                stack.enter_context(patch.object(process, "kill", side_effect=OSError("kill failure")))
                stack.enter_context(patch.object(process, "wait", side_effect=subprocess.TimeoutExpired("host", 1)))
                raise error

            with self.assertRaises(ValueError) as caught:
                self._launch("import time; time.sleep(30)", frozen_gui=True, on_started=callback)
        self.assertIs(caught.exception, error)
        self.assertIn("OSError", " ".join(error.__notes__))
        # A denied kill cannot guarantee reaping. Complete test cleanup with
        # the real methods, without claiming that the runner killed this child.
        self._emergency_reap(self.processes[0])
        self._assert_reaped(killed=True, gui=True)

    def test_process_lookup_race_still_waits_without_masking_callback_error(self):
        error = RuntimeError("callback failure")
        with ExitStack() as stack:
            def callback(pid):
                process = self.processes[0]
                real_kill = process.kill

                def exited_before_kill():
                    real_kill()
                    raise ProcessLookupError("already exited")

                stack.enter_context(patch.object(process, "kill", side_effect=exited_before_kill))
                self.wait_calls = stack.enter_context(patch.object(process, "wait", wraps=process.wait))
                raise error

            with self.assertRaises(RuntimeError) as caught:
                self._launch("import time; time.sleep(30)", on_started=callback)
        self.assertIs(caught.exception, error)
        self.assertTrue(self.wait_calls.called)
        self._assert_reaped(killed=True)

    def test_request_and_large_stderr_are_drained_concurrently(self):
        script = (
            "import sys; sys.stderr.buffer.write(b'noise' * 400000); sys.stderr.flush(); "
            "sys.stdin.read(); "
            f"sys.stdout.write({json.dumps(self.EVENT)!r})"
        )
        result = self._launch(script, request={"large": "x" * 500000})
        self.assertEqual(result.payload["status"], "success")
        self.assertEqual(len(result.stderr), self.runner.MAX_STDERR_BYTES)
        self._assert_reaped()

    def test_stdout_flood_fails_promptly_and_reaps_host(self):
        script = "import os\nwhile True: os.write(1, b'x' * 65536)"
        started = time.monotonic()
        result = self._launch(script)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.OUTPUT_TOO_LARGE)
        self.assertEqual(result.payload["data"]["stream"], "stdout")
        self.assertEqual(result.payload["data"]["max_bytes"], 4096)
        self._assert_reaped(killed=True)

    def test_exited_oversized_response_is_still_rejected(self):
        result = self._launch("import os; os.write(1, b'x' * 5000)")
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.OUTPUT_TOO_LARGE)
        self._assert_reaped()

    def test_response_exactly_at_limit_succeeds(self):
        body = json.dumps(self.EVENT)
        body += " " * (self.runner.MAX_RESPONSE_BYTES - len(body))
        result = self._launch(f"import sys; sys.stdin.read(); sys.stdout.write({body!r})")
        self.assertEqual(result.payload["status"], "success")
        self._assert_reaped()

    def test_stderr_retains_only_bounded_tail(self):
        script = (
            "import sys; sys.stdin.read(); "
            "sys.stderr.buffer.write(b'x' * 200000 + b'FINAL diagnostic'); "
            f"sys.stdout.write({json.dumps(self.EVENT)!r})"
        )
        result = self._launch(script)
        self.assertEqual(result.payload["status"], "success")
        self.assertEqual(len(result.stderr), self.runner.MAX_STDERR_BYTES)
        self.assertTrue(result.stderr.endswith("FINAL diagnostic"))
        self._assert_reaped()

    def test_multibyte_and_invalid_stderr_do_not_break_response(self):
        script = (
            "import sys; sys.stdin.read(); sys.stderr.buffer.write(('错误' * 2000).encode() + b'\\xffEND'); "
            f"sys.stdout.write({json.dumps(self.EVENT)!r})"
        )
        result = self._launch(script)
        self.assertEqual(result.payload["status"], "success")
        self.assertLessEqual(len(result.stderr), self.runner.MAX_STDERR_BYTES)
        self.assertTrue(result.stderr.endswith("\ufffdEND"))
        self._assert_reaped()

    def test_timeout_kills_waits_and_closes_pipes(self):
        self.runner.timeout_seconds = 0.15
        result = self._launch("import time; time.sleep(30)")
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.TIMEOUT)
        self._assert_reaped(killed=True)

    def test_timeout_also_handles_host_not_reading_large_request(self):
        self.runner.timeout_seconds = 0.15
        result = self._launch("import time; time.sleep(30)", request={"large": "x" * 500000})
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.TIMEOUT)
        self._assert_reaped(killed=True)

    def test_start_callback_error_reaps_host_and_preserves_exception(self):
        error = RuntimeError("history write failed")

        def failed_callback(pid):
            self.assertEqual(pid, self.processes[0].pid)
            raise error

        with self.assertRaises(RuntimeError) as caught:
            self._launch("import time; time.sleep(30)", on_started=failed_callback)
        self.assertIs(caught.exception, error)
        self._assert_reaped(killed=True)

    def test_frozen_gui_success_keeps_file_protocol_and_cleans_files(self):
        script = (
            "import json, sys; from pathlib import Path; "
            "request = Path(sys.argv[sys.argv.index('--request-file') + 1]); "
            "assert json.loads(request.read_text(encoding='utf-8')) == {'hello': '世界'}; "
            "response = Path(sys.argv[sys.argv.index('--response-file') + 1]); "
            f"response.write_text({json.dumps(self.EVENT, ensure_ascii=False)!r}, encoding='utf-8')"
        )
        result = self._launch(script, frozen_gui=True, request={"hello": "世界"})
        self.assertEqual(result.payload["status"], "success")
        self.assertIsNone(self.processes[0].stdout)
        self._assert_reaped(gui=True)

    def test_frozen_gui_response_growth_is_detected_while_running(self):
        script = (
            "import sys, time; from pathlib import Path; "
            "response = Path(sys.argv[sys.argv.index('--response-file') + 1]); "
            "response.write_bytes(b'x' * 8192); time.sleep(30)"
        )
        result = self._launch(script, frozen_gui=True)
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.OUTPUT_TOO_LARGE)
        self.assertEqual(result.payload["data"]["stream"], "response_file")
        self._assert_reaped(killed=True, gui=True)

    def test_frozen_gui_exited_oversized_response_is_rejected(self):
        script = (
            "import sys; from pathlib import Path; "
            "Path(sys.argv[sys.argv.index('--response-file') + 1]).write_bytes(b'x' * 5000)"
        )
        result = self._launch(script, frozen_gui=True)
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.OUTPUT_TOO_LARGE)
        self._assert_reaped(gui=True)

    def test_frozen_gui_timeout_cleans_files(self):
        self.runner.timeout_seconds = 0.15
        result = self._launch("import time; time.sleep(30)", frozen_gui=True)
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.TIMEOUT)
        self._assert_reaped(killed=True, gui=True)

    def test_frozen_gui_callback_failure_cleans_files(self):
        def callback(pid):
            raise ValueError("callback failed")

        with self.assertRaisesRegex(ValueError, "callback failed"):
            self._launch("import time; time.sleep(30)", frozen_gui=True, on_started=callback)
        self._assert_reaped(killed=True, gui=True)

    def test_frozen_gui_spawn_failure_cleans_files(self):
        temp_dir = self.root / "host-transfer"
        temp_dir.mkdir()
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", str(self.root / "TestBox-GUI.exe")
        ), patch("testbox.core.process_runner.tempfile.mkdtemp", return_value=str(temp_dir)), patch(
            "testbox.core.process_runner.subprocess.Popen", side_effect=OSError("launch failed")
        ):
            with self.assertRaisesRegex(OSError, "launch failed"):
                self.runner.run({}, task_id="task-1")
        self.assertFalse(temp_dir.exists())

    def test_frozen_gui_request_write_failure_cleans_files(self):
        temp_dir = self.root / "host-transfer"
        temp_dir.mkdir()
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", str(self.root / "TestBox-GUI.exe")
        ), patch("testbox.core.process_runner.tempfile.mkdtemp", return_value=str(temp_dir)), patch.object(
            Path, "write_text", side_effect=OSError("disk failure")
        ):
            with self.assertRaisesRegex(OSError, "disk failure"):
                self.runner.run({}, task_id="task-1")
        self.assertFalse(temp_dir.exists())

    def test_frozen_gui_invalid_utf8_is_structured_protocol_failure(self):
        script = (
            "import sys; from pathlib import Path; "
            "Path(sys.argv[sys.argv.index('--response-file') + 1]).write_bytes(b'\\xff')"
        )
        result = self._launch(script, frozen_gui=True)
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.HOST_PROTOCOL_ERROR)
        self._assert_reaped(gui=True)

    def test_missing_gui_response_is_protocol_failure_and_cleanup(self):
        result = self._launch("pass", frozen_gui=True)
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.HOST_PROTOCOL_ERROR)
        self._assert_reaped(gui=True)

    def test_multiple_responses_are_rejected(self):
        body = json.dumps(self.EVENT) * 2
        result = self._launch(f"import sys; sys.stdin.read(); sys.stdout.write({body!r})")
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.HOST_PROTOCOL_ERROR)
        self._assert_reaped()

    def test_crashed_host_cannot_return_success(self):
        result = self._launch(self._success_script() + "; sys.exit(7)")
        self.assertEqual(result.payload["data"]["error_code"], ErrorCode.HOST_CRASHED)
        self.assertEqual(result.payload["data"]["exit_code"], 7)
        self._assert_reaped()

    def test_pipe_read_failure_reaps_process_and_propagates(self):
        # Inject only after Popen has read its own startup error pipe.
        with ExitStack() as stack:
            def inject_after_start(pid):
                stack.enter_context(patch("testbox.core.process_runner.os.read", side_effect=OSError("pipe failure")))

            with self.assertRaisesRegex(OSError, "pipe failure"):
                self._launch("import time; time.sleep(30)", on_started=inject_after_start)
        self._assert_reaped(killed=True)


if __name__ == "__main__":
    unittest.main()
