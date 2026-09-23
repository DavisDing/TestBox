from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from testbox.core.process_runner import ProcessRunner


class ProcessRunnerTests(unittest.TestCase):
    EVENT = {
        "protocol_version": 1,
        "event": "result",
        "task_id": "task-1",
        "result": {"status": "success", "message": "ok", "data": {}, "files": [], "warnings": []},
    }

    def _run_frozen(self, executable: str):
        class FakeProcess:
            pid = 123
            returncode = 0

            def __init__(self, command):
                self.command = command

            def communicate(self, input=None, timeout=None):
                if "--response-file" in self.command:
                    response_path = Path(self.command[self.command.index("--response-file") + 1])
                    response_path.write_text(json.dumps(ProcessRunnerTests.EVENT), encoding="utf-8")
                    return "", ""
                return json.dumps(ProcessRunnerTests.EVENT), ""

        def fake_popen(command, **kwargs):
            return FakeProcess(command)

        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", executable), patch(
            "testbox.core.process_runner.subprocess.Popen", side_effect=fake_popen
        ) as popen:
            result = ProcessRunner(Path("."), timeout_seconds=1).run({}, task_id="task-1")
            self.assertEqual(result.payload["status"], "success")
            return result, popen.call_args.args[0]

    def test_frozen_gui_uses_file_protocol_with_same_executable(self):
        executable = "/install/TestBox GUI/TestBox-GUI/TestBox-GUI.exe"
        resolved_executable = str(Path(executable).resolve())
        result, command = self._run_frozen(executable)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(command[:2], [resolved_executable, "--plugin-host"])
        self.assertEqual(command[2::2], ["--request-file", "--response-file"])
        self.assertNotIn("TestBox-GUI-Host.exe", command)

    def test_frozen_cli_keeps_embedded_host_mode(self):
        executable = "/install/TestBox CLI/TestBox/TestBox.exe"
        result, command = self._run_frozen(executable)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(command, [executable, "--plugin-host"])


if __name__ == "__main__":
    unittest.main()
