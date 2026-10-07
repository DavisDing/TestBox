"""Security regressions using synthetic secrets and real Plugin Host processes."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest

from testbox.core.host import DEFAULT_MAX_LOG_BYTES, TaskLogger
from testbox.core.redaction import Redactor

ROOT = Path(__file__).resolve().parents[1]


class RedactorTests(unittest.TestCase):
    def test_compatible_sensitive_keys_case_insensitive(self):
        for marker in Redactor.SENSITIVE_KEYS:
            with self.subTest(marker=marker):
                key = f"nested_{marker.upper()}_value"
                source = {key: f"synthetic-{marker}-private", "label": "public-label"}
                redactor = Redactor(source)
                self.assertEqual(redactor.value(source), {key: "***", "label": "public-label"})
                self.assertEqual(redactor.text(f"copied={source[key]}"), "copied=***")

    def test_multiple_sources_nested_arrays_and_sensitive_containers(self):
        params = {"items": [{"password": "synthetic-password"}], "public": "public-label"}
        config = {"credentials": {"primary": ["synthetic-token", {"pin": 123456}]}, "label": "normal"}
        original = copy.deepcopy((params, config))
        redactor = Redactor(params, config)
        value = {"copies": ["synthetic-password", {"note": "synthetic-token / 123456"}], "metadata": {"api_key": 42}}
        self.assertEqual(redactor.value(value), {"copies": ["***", {"note": "*** / ***"}], "metadata": {"api_key": "***"}})
        self.assertEqual(redactor.value(config), {"credentials": "***", "label": "normal"})
        self.assertEqual((params, config), original)
        self.assertEqual(value["copies"][0], "synthetic-password")

    def test_non_sensitive_scalars_and_common_values_are_preserved(self):
        source = {"password": 1, "token": False, "secret": None, "api_key": "", "label": "public"}
        redactor = Redactor(source)
        self.assertEqual(redactor.value(source), {"password": "***", "token": "***", "secret": "***", "api_key": "***", "label": "public"})
        ordinary = {"count": 1, "enabled": False, "optional": None, "empty": "", "ratio": 1.5}
        self.assertEqual(redactor.value(ordinary), ordinary)
        self.assertEqual(redactor.text("pin=1 count=101 enabled=False optional=None"), "pin=*** count=101 enabled=False optional=None")

    def test_short_strings_do_not_erase_unrelated_words(self):
        redactor = Redactor({"password": "a", "token": "xy"})
        self.assertEqual(redactor.text("failed, api, xylophone; a / xy"), "failed, api, xylophone; *** / ***")
        self.assertEqual(redactor.value({"status": "failed", "copy": "a"}), {"status": "failed", "copy": "***"})

    def test_overlapping_values_are_replaced_longest_first_in_one_pass(self):
        redactor = Redactor({"password": "test-secret", "token": "test-secret-longer"})
        self.assertEqual(redactor.text("test-secret-longer test-secret"), "*** ***")
        self.assertEqual(redactor.text(redactor.text("test-secret")), "***")

    def test_redaction_markers_survive_repeated_host_runtime_redaction(self):
        for secret in ("*", "**", "***"):
            with self.subTest(secret=secret):
                redactor = Redactor({"token": secret})
                self.assertEqual(redactor.value({"message": "***"}), {"message": "***"})

    def test_escaped_values_and_regex_metacharacters(self):
        secret = 'test."密钥"\\next\nline'
        redactor = Redactor({"dsn": secret})
        for text in (secret, json.dumps(secret), json.dumps(secret, ensure_ascii=False), repr(secret)):
            with self.subTest(text=text):
                self.assertIn("***", redactor.text(text))
                self.assertNotIn("next", redactor.text(text))

    def test_no_sources_and_tuple_compatibility(self):
        value = {"label": "public", "tuple": (1, "two"), "password": {"nested": True}}
        self.assertEqual(Redactor().value(value), {"label": "public", "tuple": [1, "two"], "password": "***"})
        self.assertEqual(Redactor().text("ordinary text"), "ordinary text")

    def test_dictionary_keys_are_preserved_even_when_matching_a_secret(self):
        value = {"status": "success", "message": "status", "data": {"status": "status"}}
        self.assertEqual(Redactor({"token": "status"}).value(value), {"status": "success", "message": "***", "data": {"status": "***"}})


class TaskLoggerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "task.log"

    def test_utf8_byte_limit_and_once_only_notice(self):
        logger = TaskLogger(self.path, Redactor({"password": "synthetic-password"}), max_bytes=128)
        logger.info("synthetic-password" + "界" * 100)
        first = self.path.read_bytes()
        for _ in range(100):
            logger.error("synthetic-password")
        self.assertEqual(self.path.read_bytes(), first)
        self.assertLessEqual(len(first), 128)
        text = first.decode("utf-8")
        self.assertNotIn("synthetic-password", text)
        self.assertIn("***", text)
        self.assertEqual(text.count(TaskLogger.TRUNCATION_NOTICE), 1)
        self.assertTrue(text.endswith(TaskLogger.TRUNCATION_NOTICE))

    def test_threaded_logging_respects_total_quota(self):
        logger = TaskLogger(self.path, max_bytes=4096)
        threads = [threading.Thread(target=lambda: [logger.info("界" * 50) for _ in range(50)]) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertLessEqual(self.path.stat().st_size, 4096)
        self.assertEqual(self.path.read_text(encoding="utf-8").count(TaskLogger.TRUNCATION_NOTICE), 1)

    def test_reopening_truncated_log_does_not_append_another_notice(self):
        TaskLogger(self.path, max_bytes=128).info("x" * 1000)
        first = self.path.read_bytes()
        TaskLogger(self.path, max_bytes=128).warning("more content")
        self.assertEqual(self.path.read_bytes(), first)

    def test_existing_log_counts_toward_quota(self):
        self.path.write_bytes(b"INFO existing\n")
        TaskLogger(self.path, max_bytes=128).info("x" * 1000)
        self.assertLessEqual(self.path.stat().st_size, 128)
        self.assertTrue(self.path.read_bytes().startswith(b"INFO existing\n"))

    def test_minimum_quota_and_invalid_limits(self):
        minimum = len(TaskLogger.TRUNCATION_NOTICE.encode("utf-8"))
        TaskLogger(self.path, max_bytes=minimum).info("content")
        self.assertEqual(self.path.read_text(encoding="utf-8"), TaskLogger.TRUNCATION_NOTICE)
        for invalid in (0, -1, minimum - 1, True, 128.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                TaskLogger(self.path, max_bytes=invalid)


class HostSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plugin = self.root / "plugin"
        self.plugin.mkdir()
        self.workspace = self.root / "workspace"
        for directory in ("input", "output", "logs"):
            (self.workspace / directory).mkdir(parents=True)
        self.request = {
            "protocol_version": 1, "task_id": "synthetic-security-test",
            "plugin_path": str(self.plugin), "entry": "main:Plugin", "command": "test.security",
            "params": {"nested": [{"api_key": "synthetic-param-secret"}], "count": 7, "label": "ordinary-label"},
            "config": {"nested": {"password": "synthetic-config-secret"}},
            "workspace": str(self.workspace),
        }

    def run_host(self, body: str, *, file_protocol: bool = False, init_body: str | None = None):
        source = '''from testbox.sdk import PluginError, Result
class Plugin:
    def init(self, context):
        self.context = context
    def destroy(self):
        self.context.logger.warning("destroy " + self.context.config["nested"]["password"])
    def execute(self, command, params):
'''
        if init_body is not None:
            source = source.replace("        self.context = context\n", "        self.context = context\n" + textwrap.indent(textwrap.dedent(init_body).strip() + "\n", "        "))
        (self.plugin / "main.py").write_text(source + textwrap.indent(textwrap.dedent(body).strip() + "\n", "        "), encoding="utf-8")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), environment.get("PYTHONPATH"))))
        if file_protocol:
            request_path = self.root / "request.json"
            response_path = self.root / "response.json"
            request_path.write_text(json.dumps(self.request), encoding="utf-8")
            command = [sys.executable, "-c", "from pathlib import Path; from testbox.core.host import main; import sys; main(request_path=Path(sys.argv[1]), response_path=Path(sys.argv[2]))", str(request_path), str(response_path)]
            process = subprocess.run(command, cwd=self.root, env=environment, capture_output=True, text=True, timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stdout, "")
            response = response_path.read_text(encoding="utf-8")
        else:
            process = subprocess.run([sys.executable, "-m", "testbox.core.host"], input=json.dumps(self.request), cwd=self.root, env=environment, capture_output=True, text=True, timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue(process.stdout.isascii())
            response = process.stdout
        self.assertEqual(process.stderr, "")
        payload = json.loads(response)
        self.assertEqual(payload["protocol_version"], 1)
        self.assertEqual(payload["event"], "result")
        self.assertEqual(payload["task_id"], self.request["task_id"])
        log = (self.workspace / "logs" / "task.log").read_text(encoding="utf-8")
        for secret in ("synthetic-param-secret", "synthetic-config-secret"):
            self.assertNotIn(secret, json.dumps({key: payload["result"][key] for key in ("message", "data", "warnings")}))
            self.assertNotIn(secret, log)
        return payload["result"], log

    def test_success_redacts_log_and_all_result_surfaces_without_mutating_inputs(self):
        result, log = self.run_host('''
            secret = params["nested"][0]["api_key"]
            config_secret = self.context.config["nested"]["password"]
            self.context.logger.info("params=" + str(params))
            self.context.logger.warning("config=" + str(self.context.config))
            self.context.logger.error("copies " + secret + " / " + config_secret)
            assert secret == "synthetic-param-secret"
            assert config_secret == "synthetic-config-secret"
            return Result("success", "done " + secret, data={"copies": [config_secret, {"note": secret}], "API_KEY": "new-unlisted-value", "count": params["count"], "label": params["label"]}, warnings=["warning " + secret], files=["ordinary.txt"])
        ''')
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], "done ***")
        self.assertEqual(result["data"], {"copies": ["***", {"note": "***"}], "API_KEY": "***", "count": 7, "label": "ordinary-label"})
        self.assertEqual(result["warnings"], ["warning ***"])
        self.assertEqual(result["files"], ["ordinary.txt"])
        self.assertIn("INFO params=", log)
        self.assertIn("WARNING config=", log)
        self.assertIn("ERROR copies *** / ***", log)
        self.assertIn("WARNING destroy ***", log)

    def test_unexpected_exception_redacts_traceback_and_exception_message(self):
        result, log = self.run_host('''
            raise RuntimeError(params["nested"][0]["api_key"] + " / " + self.context.config["nested"]["password"])
        ''')
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["data"]["exception_type"], "RuntimeError")
        self.assertEqual(result["data"]["exception_message"], "*** / ***")
        self.assertIn("Traceback", log)
        self.assertIn("RuntimeError: *** / ***", log)
        self.assertIn("destroy ***", log)

    def test_plugin_error_redacts_code_message_and_nested_details(self):
        result, log = self.run_host('''
            secret = params["nested"][0]["api_key"]
            raise PluginError("CODE_" + secret, "failed " + secret, details={"records": [{"echo": self.context.config["nested"]["password"]}], "credential": {"value": "unlisted-secret"}, "count": 7})
        ''')
        self.assertEqual(result["message"], "failed ***")
        self.assertEqual(result["data"]["error_code"], "CODE_***")
        self.assertEqual(result["data"]["details"], {"records": [{"echo": "***"}], "credential": "***", "count": 7})
        self.assertNotIn("unlisted-secret", json.dumps(result))
        self.assertIn("ERROR failed ***", log)

    def test_log_flood_is_bounded_and_single_result_still_returns(self):
        result, log = self.run_host('''
            secret = params["nested"][0]["api_key"]
            self.context.logger.info(secret + "界" * 500000)
            for index in range(200):
                self.context.logger.error(secret + "界" * 1000)
            return Result("success", "after quota " + secret, data={"count": 200})
        ''')
        self.assertLessEqual((self.workspace / "logs" / "task.log").stat().st_size, DEFAULT_MAX_LOG_BYTES)
        self.assertEqual(log.count(TaskLogger.TRUNCATION_NOTICE), 1)
        self.assertTrue(log.endswith(TaskLogger.TRUNCATION_NOTICE))
        self.assertEqual(result["message"], "after quota ***")
        self.assertEqual(result["status"], "success")

    def test_utf8_file_protocol_uses_same_redaction(self):
        result, log = self.run_host('''
            self.context.logger.info(self.context.config["nested"]["password"])
            return Result("success", "完成 " + params["nested"][0]["api_key"])
        ''', file_protocol=True)
        self.assertEqual(result["message"], "完成 ***")
        self.assertIn("INFO ***", log)

    def test_init_failure_is_redacted_and_destroy_still_runs(self):
        result, log = self.run_host('return Result("success", "must not execute")', init_body='''
            raise ValueError("init " + context.config["nested"]["password"])
        ''')
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["data"]["exception_message"], "init ***")
        self.assertIn("destroy ***", log)

    def test_structural_secrets_do_not_change_protocol_status_keys_or_output_paths(self):
        self.request["config"].update({
            "api_key": "status", "token": "message", "secret": "success",
            "credential": "result", "password": 1,
            "connection": self.request["task_id"],
        })
        result, log = self.run_host('''
            path = self.context.files.write_text(self.context.task.id + ".txt", "synthetic public output")
            self.context.logger.info("status success message result")
            return Result("success", "status success message result", data={"status": "status", "message": "message", "count": 7}, files=[path], warnings=["result"])
        ''')
        self.assertEqual(set(result), {"status", "message", "data", "files", "warnings"})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], "*** *** *** ***")
        self.assertEqual(result["data"], {"status": "***", "message": "***", "count": 7})
        self.assertEqual(result["warnings"], ["***"])
        self.assertEqual(result["files"], [self.request["task_id"] + ".txt"])
        self.assertTrue((self.workspace / "output" / result["files"][0]).is_file())
        self.assertIn("INFO *** *** *** ***", log)


if __name__ == "__main__":
    unittest.main()
