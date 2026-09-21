from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from testbox.core.process_runner import ProcessRunner


class ProcessRunnerTests(unittest.TestCase):
    def _run_frozen(self, executable: str) -> list[str]:
        event = {
            "protocol_version": 1,
            "event": "result",
            "task_id": "task-1",
            "result": {"status": "success", "message": "ok", "data": {}, "files": [], "warnings": []},
        }

        class FakeProcess:
            pid = 123
            returncode = 0

            def communicate(self, input=None, timeout=None):
                return json.dumps(event), ""

        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", executable), patch(
            "testbox.core.process_runner.subprocess.Popen", return_value=FakeProcess()
        ) as popen:
            result = ProcessRunner(Path("."), timeout_seconds=1).run({}, task_id="task-1")

        self.assertEqual(result.payload["status"], "success")
        return popen.call_args.args[0]

    def test_frozen_gui_uses_console_host_companion(self):
        command = self._run_frozen(r"/install/TestBox GUI/TestBox-GUI/TestBox-GUI.exe")
        self.assertEqual(command, [r"/install/TestBox GUI/TestBox-GUI-Host.exe"])

    def test_frozen_cli_keeps_embedded_host_mode(self):
        command = self._run_frozen(r"/install/TestBox CLI/TestBox/TestBox.exe")
        self.assertEqual(command, [r"/install/TestBox CLI/TestBox/TestBox.exe", "--plugin-host"])


if __name__ == "__main__":
    unittest.main()
