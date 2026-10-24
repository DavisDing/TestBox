"""Behavioral regressions for the October 24 local security audit.

All probes use temporary roots and synthetic credentials only.
"""
from __future__ import annotations

import csv
import io
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from testbox.core.locks import TaskLifecycleLock
from testbox.core.plugin_packages import PluginPackageError, uninstall_plugin
from testbox.core.process_runner import host_environment
from testbox.core.runtime import Runtime
from testbox.core.schema_validator import SchemaValidationError, SchemaValidator
from testbox.file_safety import check_excel_archive, read_excel_xml, spreadsheet_cell
from testbox import updater

ROOT = Path(__file__).resolve().parents[1]


def make_probe(root: Path) -> Path:
    directory = root / "plugins" / "audit-probe"
    (directory / "src").mkdir(parents=True)
    (directory / "manifest.yaml").write_text(json.dumps({
        "schema_version": 1, "name": "audit-probe", "version": "1.0.0",
        "description": "Synthetic audit fixture", "category": "test",
        "core_compatibility": ">=1.0.0,<2.0.0", "entry": "src.main:Plugin",
        "commands": [{"name": "audit.probe"}],
        "capabilities": {"concurrency": True, "network": False,
                         "filesystem": "output-only", "resources": []},
    }), encoding="utf-8")
    (directory / "src/main.py").write_text('''
import json, os, time
from testbox.sdk import Result
class Plugin:
    def init(self, context): self.context = context
    def execute(self, command, params):
        if self.context.config.get("sleep"):
            time.sleep(30)
        probe = {key: os.environ.get(key) for key in
                 ("AUDIT_TOKEN", "AUDIT_SECRET", "AUDIT_PROXY", "TESTBOX_AUDIT_PROBE_TOKEN")}
        probe["configured"] = self.context.config.get("token")
        name = self.context.files.write_text("probe.json", json.dumps(probe))
        return Result("success", "done", files=[name])
    def destroy(self): pass
''', encoding="utf-8")
    return directory


class AuditSecurityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="testbox-security-audit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.probe = make_probe(self.root)
        self.runtime = Runtime(self.root)
        self.addCleanup(self.runtime.close)

    def test_ambient_credentials_absent_from_real_host_and_artifact(self):
        synthetic = {"AUDIT_TOKEN": "SYNTHETIC_TOKEN", "AUDIT_SECRET": "SYNTHETIC_SECRET",
                     "AUDIT_PROXY": "SYNTHETIC_PROXY_PASSWORD",
                     "TESTBOX_AUDIT_PROBE_TOKEN": "SYNTHETIC_EXPLICIT_TOKEN"}
        with patch.dict(os.environ, synthetic):
            task_id, result = self.runtime.run("audit.probe", {})
        self.assertEqual(result.status, "success", result.message)
        artifact = json.loads(self.runtime.get_task_output_path(task_id, result.files[0]).read_text())
        self.assertTrue(all(artifact[key] is None for key in synthetic))
        self.assertEqual(artifact["configured"], synthetic["TESTBOX_AUDIT_PROBE_TOKEN"])
        # Explicitly granted plugin config remains usable; arbitrary artifact
        # bodies are not a security redaction boundary.
        self.assertNotIn(synthetic["TESTBOX_AUDIT_PROBE_TOKEN"],
                         (self.runtime.workspace_dir / task_id / "manifest.json").read_text())

    def test_environment_drops_loader_and_python_injection_settings(self):
        with patch.dict(os.environ, {"PYTHONPATH": "/synthetic", "PYTHONSTARTUP": "/synthetic",
                                     "LD_PRELOAD": "/synthetic", "DYLD_INSERT_LIBRARIES": "/synthetic"}):
            environment = host_environment()
        self.assertTrue({"PYTHONPATH", "PYTHONSTARTUP", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"}.isdisjoint(environment))

    def test_install_requires_explicit_trust_and_preview_explains_boundary(self):
        archive = self.runtime.package_plugin(self.probe, self.root / "probe.zip")
        preview = self.runtime.preview_plugin_install(archive)
        self.assertFalse(preview["signature_verified"])
        self.assertIn("不是安全沙箱", preview["security_warning"])
        self.assertIn("filesystem", preview["capabilities"])
        with self.assertRaisesRegex(PluginPackageError, "trust_code"):
            self.runtime.install_plugin(archive, force=True)
        self.runtime.install_plugin(archive, force=True, trust_code=True)

    def cli(self, *arguments):
        environment = dict(os.environ, PYTHONPATH=str(ROOT), XDG_DATA_HOME=str(self.root / "user"),
                           LOCALAPPDATA=str(self.root / "user"))
        return subprocess.run([sys.executable, "-m", "testbox.cli", "--json", *arguments], cwd=self.root,
                              env=environment, capture_output=True, text=True, timeout=15)

    def test_cli_install_requires_flag_and_can_install_after_trust(self):
        archive = self.runtime.package_plugin(self.probe, self.root / "probe.zip")
        denied = self.cli("plugin", "install", str(archive), "--force")
        self.assertEqual(denied.returncode, 3, denied.stderr)
        self.assertIn("--trust-code", json.loads(denied.stderr)["error"]["message"])
        accepted = self.cli("plugin", "install", str(archive), "--force", "--trust-code")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["name"], "audit-probe")

    def test_cli_can_read_and_export_core_report(self):
        task_id, result = self.runtime.run("audit.probe", {})
        self.assertEqual(result.status, "success")
        shown = self.cli("task", "report", task_id)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["report"], self.runtime.get_task_report(task_id))
        target = self.root / "cli/report.md"
        saved = self.cli("task", "report", task_id, "--output", str(target))
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(target.read_text(), self.runtime.get_task_report(task_id))

    def test_source_resources_cannot_be_uninstalled_by_facade_or_package_api(self):
        self.runtime.plugins_dir = ROOT / "plugins"
        self.runtime.bundled_plugins_dir = ROOT / "plugins"
        self.runtime.manager.plugins_dirs = [ROOT / "plugins"]
        self.assertFalse(self.runtime.can_uninstall_plugin("data-generator"))
        with self.assertRaisesRegex(PluginPackageError, "内置"):
            self.runtime.uninstall_plugin("data-generator")
        with self.assertRaisesRegex(PluginPackageError, "内置"):
            uninstall_plugin("data-generator", ROOT / "plugins")
        self.assertTrue((ROOT / "plugins/data-generator/manifest.yaml").is_file())

    def test_source_runtime_installs_overrides_outside_resource_tree(self):
        source_root = self.root / "source-checkout"
        shutil.copytree(self.probe, source_root / "plugins/audit-probe")
        user_data = self.root / "user-data"
        with patch("testbox.core.runtime.__file__", str(source_root / "testbox/core/runtime.py")), \
             patch.object(Runtime, "_application_root", return_value=source_root), \
             patch.object(Runtime, "_user_data_dir", return_value=user_data):
            runtime = Runtime()
            try:
                self.assertEqual(runtime.plugins_dir, user_data / "plugins")
                self.assertEqual(runtime.bundled_plugins_dir, source_root / "plugins")
                self.assertFalse(runtime.can_uninstall_plugin("audit-probe"))
                runtime.install_plugin(self.probe, trust_code=True)
                self.assertEqual(runtime.get_command("audit.probe").path, user_data / "plugins/audit-probe")
                runtime.uninstall_plugin("audit-probe")
                self.assertEqual(runtime.get_command("audit.probe").path, source_root / "plugins/audit-probe")
            finally:
                runtime.close()

    def test_hidden_recovery_copies_never_override_or_run(self):
        hidden = self.root / "plugins/.audit-probe.previous-deadbeef"
        shutil.copytree(self.probe, hidden)
        marker = self.root / "hidden-executed"
        (hidden / "src/main.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        self.runtime.reload_plugins()
        self.assertEqual(self.runtime.get_command("audit.probe").path, self.probe)
        _, result = self.runtime.run("audit.probe", {})
        self.assertEqual(result.status, "success")
        self.assertFalse(marker.exists())
        shutil.rmtree(self.probe)
        self.runtime.reload_plugins()
        self.assertNotIn("audit.probe", self.runtime.list_commands())

    def test_symlink_plugin_alias_never_loads(self):
        alias = self.root / "plugins/alias"
        try:
            alias.symlink_to(self.probe, target_is_directory=True)
        except OSError:
            self.skipTest("Host cannot create symlinks")
        self.runtime.reload_plugins()
        self.assertIn(str(alias), self.runtime.manager.unavailable)
        self.assertFalse(self.runtime.can_uninstall_plugin("alias"))

    def test_history_terminal_conflict_is_visible_without_overwriting_terminal(self):
        finish = self.runtime.history.finish
        def race(task_id, **kwargs):
            with self.runtime.history.connection:
                self.runtime.history.connection.execute(
                    "UPDATE task_history SET status='ABANDONED' WHERE id=?", (task_id,))
            return finish(task_id, **kwargs)
        with patch.object(self.runtime.history, "finish", side_effect=race):
            task_id, result = self.runtime.run("audit.probe", {})
        self.assertEqual(result.status, "success")
        self.assertEqual(self.runtime.get_task(task_id)["status"], "ABANDONED")
        self.assertIn("历史同步失败", " ".join(result.warnings))
        self.assertIn("历史同步失败", " ".join(self.runtime.get_task_result(task_id)["warnings"]))

    def test_report_text_cannot_inject_markdown_structure_or_html(self):
        from testbox.core.report import write_report
        from testbox.sdk import Result
        report = self.root / "report.md"
        write_report(report, "task", self.runtime.get_command("audit.probe"),
                     Result("success", "text\n## forged\n<script>alert(1)</script>",
                            files=["`\n## injected"], warnings=["<img src=x onerror=bad>"]))
        content = report.read_text()
        self.assertNotIn("\n## forged", content)
        self.assertNotIn("<script>", content)
        self.assertNotIn("<img", content)
        self.assertEqual(content.count("\n## "), 2)

    def test_report_exports_through_core_without_widening_output_allowlist(self):
        task_id, _ = self.runtime.run("audit.probe", {})
        target = self.runtime.commit_task_report(task_id, self.root / "export/report.md")
        self.assertEqual(target.read_text(), self.runtime.get_task_report(task_id))
        with self.assertRaises(ValueError):
            self.runtime.commit_output(task_id, "../report.md", self.root / "bad.md")
        report = self.runtime.workspace_dir / task_id / "report.md"
        report.unlink()
        try:
            report.symlink_to(self.root / "export/report.md")
        except OSError:
            self.skipTest("Host cannot create symlinks")
        with self.assertRaises(ValueError):
            self.runtime.commit_task_report(task_id, self.root / "bad.md")

    def test_task_lifecycle_locks_are_reclaimed_after_success_and_cleanup(self):
        self.runtime.run("audit.probe", {})
        self.assertEqual(list((self.runtime.workspace_dir / ".locks").glob("task-20*.lock")), [])
        self.assertEqual(self.runtime.clean_workspace(date.today() + timedelta(days=1)), 1)
        self.assertEqual(list((self.runtime.workspace_dir / ".locks").glob("task-20*.lock")), [])

    def test_task_lock_reclamation_keeps_cross_process_exclusion(self):
        lock_path = self.root / "locks/task-synthetic.lock"
        lock = TaskLifecycleLock(lock_path)
        lock.acquire()
        self.addCleanup(lock.release)
        script = '''import sys
from pathlib import Path
from testbox.core.locks import TaskLifecycleLock
lock = TaskLifecycleLock(Path(sys.argv[1]))
acquired = lock.try_acquire()
if acquired: lock.release()
sys.exit(0 if acquired else 7)
'''
        def peer():
            return subprocess.run([sys.executable, "-c", script, str(lock_path)], cwd=ROOT,
                                  capture_output=True, timeout=10)
        self.assertEqual(peer().returncode, 7)
        lock.release()
        self.assertFalse(lock_path.exists())
        self.assertEqual(peer().returncode, 0)
        self.assertFalse(lock_path.exists())

    def test_cancel_while_waiting_for_plugin_lock_does_not_start_host(self):
        from testbox.core.locks import PluginExecutionLock
        self.runtime.get_command("audit.probe").capabilities["concurrency"] = False
        held = PluginExecutionLock(self.runtime.workspace_dir / ".locks/audit-probe.lock")
        held.acquire()
        self.addCleanup(held.release)
        token = threading.Event()
        timer = threading.Timer(0.15, token.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with patch.object(self.runtime.process_runner, "run") as launch:
            task_id, result = self.runtime.run("audit.probe", {}, cancel_event=token)
        launch.assert_not_called()
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.runtime.get_task(task_id)["status"], "CANCELLED")
        self.assertEqual(self.runtime.clean_workspace(date.today() + timedelta(days=1)), 1)
        self.assertEqual(list((self.runtime.workspace_dir / ".locks").glob("task-20*.lock")), [])

    def test_cancel_before_spawn_saves_cancelled_result_and_history(self):
        token = threading.Event()
        token.set()
        with patch.object(self.runtime.process_runner, "run") as launch:
            task_id, result = self.runtime.run("audit.probe", {}, cancel_event=token)
        launch.assert_not_called()
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.runtime.get_task(task_id)["status"], "CANCELLED")

    def test_runtime_cancel_stops_a_real_running_host(self):
        (self.probe / "config").mkdir()
        (self.probe / "config/config.yaml").write_text("sleep: true\n")
        self.runtime.process_runner.timeout_seconds = 15
        started = threading.Event()
        real_run = self.runtime.process_runner.run
        def launch(request, *, task_id, on_started):
            def observed(pid):
                on_started(pid)
                started.set()
            return real_run(request, task_id=task_id, on_started=observed)
        def cancel():
            if started.wait(5):
                self.runtime.cancel()
        helper = threading.Thread(target=cancel)
        helper.start()
        before = time.monotonic()
        with patch.object(self.runtime.process_runner, "run", side_effect=launch):
            task_id, result = self.runtime.run("audit.probe", {})
        helper.join(timeout=2)
        self.assertFalse(helper.is_alive())
        self.assertLess(time.monotonic() - before, 8)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.runtime.get_task(task_id)["status"], "CANCELLED")

    def test_schema_array_bounds_pattern_and_unsupported_assertions(self):
        validator = SchemaValidator()
        schema = {"type": "object", "properties": {
            "rows": {"type": "array", "minItems": 1, "maxItems": 2,
                     "items": {"type": "string", "pattern": "^[A-Z]+$"}}}}
        self.assertEqual(validator.validate(schema, {"rows": ["ABC"]}), {"rows": ["ABC"]})
        for rows in ([], ["A", "B", "C"], ["abc"]):
            with self.subTest(rows=rows), self.assertRaises(SchemaValidationError):
                validator.validate(schema, {"rows": rows})
        with self.assertRaises(SchemaValidationError) as caught:
            validator.validate({"type": "object", "oneOf": [{}, {}]}, {})
        self.assertEqual(caught.exception.code, "SCHEMA_UNSUPPORTED")

    def test_excel_limits_apply_before_xml_parser_and_reject_entities(self):
        source = self.root / "tiny.xlsx"
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/worksheets/sheet1.xml", b"x" * 2048)
        from testbox.sdk import PluginError
        with zipfile.ZipFile(source) as archive, patch("testbox.file_safety.MAX_EXCEL_BYTES", 1024):
            with self.assertRaises(PluginError) as caught:
                check_excel_archive(archive)
            self.assertEqual(caught.exception.code, "INPUT_LIMIT_EXCEEDED")
        with zipfile.ZipFile(source) as archive, patch("testbox.file_safety.MAX_XML_BYTES", 1024):
            with self.assertRaises(PluginError):
                read_excel_xml(archive, "xl/worksheets/sheet1.xml")
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("x.xml", '<!DOCTYPE x [<!ENTITY a "tiny">]><x>&a;</x>'.encode("utf-16"))
        with zipfile.ZipFile(source) as archive, self.assertRaises(PluginError):
            read_excel_xml(archive, "x.xml")

    def test_formula_cells_preserve_numbers_and_neutralize_prefixes(self):
        for text in ("=1+1", " +1", "-formula", "@SUM(1)", "\ttext", "\rtext", "\n=1"):
            self.assertEqual(spreadsheet_cell(text), "'" + text)
        self.assertEqual(spreadsheet_cell(-12), -12)
        self.assertEqual(spreadsheet_cell("safe"), "safe")

    def load_official(self, name):
        spec = importlib.util.spec_from_file_location("audit_" + name.replace("-", "_"), ROOT / "plugins" / name / "src/main.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        return module

    def test_both_lightweight_excel_readers_reject_expansion_before_parsing(self):
        from testbox.sdk import PluginError
        source = self.root / "oversized.xlsx"
        with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/worksheets/sheet1.xml", b"x" * 2048)
        for name, method in (("data-generator", "_read_excel_rows"), ("sql-select", "read_xlsx")):
            module = self.load_official(name)
            with self.subTest(name=name), patch("testbox.file_safety.MAX_XML_BYTES", 1024):
                with self.assertRaises(PluginError) as caught:
                    getattr(module, method)(source)
                self.assertEqual(caught.exception.code, "INPUT_LIMIT_EXCEEDED")

    def test_generator_csv_and_txt_neutralize_headers_and_values(self):
        module = self.load_official("data-generator")
        for delimiter in (",", "\t"):
            content = module.delimited_text([{"=header": " =1+1", "number": -5}], delimiter, True)
            rows = list(csv.reader(io.StringIO(content), delimiter=delimiter))
            self.assertEqual(rows, [["'=header", "number"], ["' =1+1", "-5"]])

    def test_parser_csv_neutralizes_comments_in_real_host(self):
        shutil.copytree(ROOT / "plugins/sql-parser", self.root / "plugins/sql-parser")
        self.runtime.reload_plugins()
        source = self.root / "schema.sql"
        source.write_text("CREATE TABLE sample (id INT COMMENT '=1+1');", encoding="utf-8")
        task_id, result = self.runtime.run("sql.parse", {"input": str(source), "format": "csv"})
        self.assertEqual(result.status, "success", result.message)
        output = self.runtime.get_task_output_path(task_id, result.files[0])
        rows = list(csv.DictReader(io.StringIO(output.read_text(encoding="utf-8"))))
        self.assertEqual(rows[0]["comment"], "'=1+1")


class AuditUpdaterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="testbox-update-interruption-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.release = self.root / "release"
        self.release.mkdir()
        self.install = self.root / "install"
        for name in ("a.txt", "b.txt", "remove.txt"):
            (self.release / name).write_text("old-" + name)
        updater.create_update_package(self.release, version="1.0.0", component="cli",
                                      output=self.root / "old.zip", manifest_output=self.root / "old.json")
        updater.apply_update(self.install, self.root / "old.zip")
        (self.install / "user.txt").write_text("preserve me")
        (self.release / "a.txt").write_text("new-a")
        (self.release / "b.txt").write_text("new-b")
        (self.release / "remove.txt").unlink()
        (self.release / "new.txt").write_text("new")
        self.package = self.root / "new.zip"
        updater.create_update_package(self.release, version="1.0.1", component="cli",
                                      output=self.package, manifest_output=self.root / "new.json",
                                      previous_manifest=self.root / "old.json")

    def interrupted(self, operation, filename):
        script = '''
import os, sys
from pathlib import Path
from testbox import updater
install, package, op, filename = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
original = getattr(Path, op)
def terminate(path, *args, **kwargs):
    result = original(path, *args, **kwargs)
    target = Path(args[0]) if op == "replace" else path
    if target == install / filename:
        os._exit(73)
    return result
setattr(Path, op, terminate)
updater.apply_update(install, package)
'''
        process = subprocess.run([sys.executable, "-c", script, str(self.install), str(self.package),
                                  operation, filename], cwd=ROOT, capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 73, process.stderr.decode())
        self.assertTrue((self.install / updater.JOURNAL_NAME).is_file())

    def test_restart_rolls_back_after_replace_then_can_apply_again(self):
        self.interrupted("replace", "a.txt")
        self.assertTrue(updater.recover_interrupted_update(self.install))
        self.assertEqual((self.install / "a.txt").read_text(), "old-a.txt")
        updater.apply_update(self.install, self.package)
        self.assertEqual((self.install / "a.txt").read_text(), "new-a")
        self.assertEqual((self.install / "user.txt").read_text(), "preserve me")

    def test_restart_recovers_deletion_and_removes_new_files(self):
        self.interrupted("unlink", "remove.txt")
        updater.recover_interrupted_update(self.install)
        self.assertEqual((self.install / "remove.txt").read_text(), "old-remove.txt")
        self.assertFalse((self.install / "new.txt").exists())
        self.assertEqual(json.loads((self.install / updater.MANIFEST_NAME).read_text())["version"], "1.0.0")

    def test_restart_after_manifest_commit_keeps_completed_new_version(self):
        self.interrupted("replace", updater.MANIFEST_NAME)
        self.assertTrue(updater.recover_interrupted_update(self.install))
        self.assertEqual((self.install / "a.txt").read_text(), "new-a")
        self.assertFalse((self.install / "remove.txt").exists())
        self.assertFalse((self.install / updater.JOURNAL_NAME).exists())
        self.assertFalse(updater.recover_interrupted_update(self.install))

    def test_modified_recovery_target_fails_without_erasing_user_changes(self):
        self.interrupted("replace", "a.txt")
        (self.install / "a.txt").write_text("modified after interruption")
        with self.assertRaisesRegex(ValueError, "已被修改"):
            updater.recover_interrupted_update(self.install)
        self.assertEqual((self.install / "a.txt").read_text(), "modified after interruption")
        self.assertTrue((self.install / updater.JOURNAL_NAME).is_file())

    def test_insecure_urls_and_redirects_rejected_before_network(self):
        for url in ("file:///tmp/update.json", "http://example.test/update.json",
                    "ftp://example.test/a", "https://user:password@example.test/a", "https://example.test/\npath"):
            with self.subTest(url=url), patch.object(updater.urllib.request, "build_opener") as network:
                with self.assertRaises(ValueError):
                    updater.download(url, self.root / "download")
                network.assert_not_called()
        with self.assertRaises(ValueError):
            updater._HTTPSRedirect().redirect_request(None, None, 302, "", {}, "http://example.test/a")


if __name__ == "__main__":
    unittest.main()
