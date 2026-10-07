"""Cross-platform regressions for the 2026-10-07 Actions test failures."""
from __future__ import annotations

import csv
import io
import importlib.util
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from testbox.core.runtime import Runtime
from testbox.sdk import SafeFiles, read_dataset

ROOT = Path(__file__).resolve().parents[1]


class CiPortabilityTests(unittest.TestCase):
    def test_gui_modules_collect_without_optional_desktop_dependency(self):
        # -S excludes site-packages, exercising the same lack of PySide6 as the
        # package job without changing the user's installed environment.
        script = '''import sys, unittest
sys.path[:0] = sys.argv[1:3]
loader = unittest.TestLoader()
suite = loader.loadTestsFromNames([
    "test_gui_preview", "test_gui_office_convert", "test_gui_runtime"
])
expected = suite.countTestCases()
assert expected > 0, "GUI test discovery returned no tests"
result = unittest.TextTestRunner().run(suite)
assert result.wasSuccessful(), "optional dependency caused test import errors"
assert result.testsRun == expected, (result.testsRun, expected)
assert len(result.skipped) == result.testsRun, result.skipped
'''
        process = subprocess.run(
            [sys.executable, "-S", "-c", script, str(ROOT), str(ROOT / "tests")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_sdk_preserves_csv_record_and_embedded_newlines_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = SafeFiles(root)
            original_write = Path.write_text

            def windows_write(path, content, encoding=None, errors=None, newline=None):
                # Simulate Windows TextIO's default translation on every host.
                return original_write(path, content, encoding=encoding, errors=errors,
                                      newline="\r\n" if newline is None else newline)

            for separator in ("\n", "\r\n", "\r"):
                for cell in ("a\nb", "a\r\nb", "a\rb"):
                    with self.subTest(separator=repr(separator), cell=repr(cell)):
                        stream = io.StringIO(newline="")
                        csv.writer(stream, lineterminator=separator,
                                   quoting=csv.QUOTE_ALL).writerows([["value"], [cell]])
                        content = stream.getvalue()
                        with patch.object(Path, "write_text", windows_write):
                            files.write_text("report.csv", content, encoding="utf-8-sig")
                        output = root / "report.csv"
                        self.assertEqual(output.read_bytes(), content.encode("utf-8-sig"))
                        parsed = read_dataset(output)
                        self.assertEqual(parsed["rows"], [{"value": cell}])
                        self.assertEqual(parsed["locations"][0]["row"], 2)

    def test_office_package_fixture_closes_database_before_temporary_cleanup(self):
        # Reproduce Windows' open-file cleanup prohibition on any platform;
        # exercise the actual packaging test's lifecycle, not a mock success.
        spec = importlib.util.spec_from_file_location(
            "office_distribution_cleanup_regression", ROOT / "tests" / "test_office_distribution.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        runtimes = []
        original_cleanup = tempfile.TemporaryDirectory.cleanup

        def tracked_runtime(root):
            runtime = Runtime(root)
            runtimes.append(runtime)
            return runtime

        def windows_cleanup(directory):
            if Path(directory.name).name.startswith("testbox-office-package-"):
                for runtime in runtimes:
                    with self.assertRaises(sqlite3.ProgrammingError):
                        runtime.history.connection.execute("SELECT 1")
            return original_cleanup(directory)

        case = module.OfficeDistributionTests(
            "test_package_preview_install_inspect_without_system_dependency"
        )
        with patch.object(module, "Runtime", tracked_runtime), patch.object(
            tempfile.TemporaryDirectory, "cleanup", windows_cleanup
        ):
            result = unittest.TestResult()
            case.run(result)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(len(runtimes), 1)

    def test_log_tail_normalizes_platform_newlines_before_character_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "task.log"
            for separator in ("\n", "\r\n", "\r"):
                with self.subTest(separator=repr(separator)):
                    log.write_bytes(separator.join(["line one", "line two", ""]).encode())
                    self.assertEqual(Runtime._read_log_tail(log, 9), "line two\n")
                    self.assertEqual(Runtime._read_log_tail(log, 0), "")
                    self.assertEqual(Runtime._read_log_tail(log, 1), "\n")
            log.write_bytes("标题\r\n第二行\r\n".encode("utf-8"))
            self.assertEqual(Runtime._read_log_tail(log, 4), "第二行\n")


if __name__ == "__main__":
    unittest.main()
