"""Cross-platform runner harness tests; NOT Windows native installer acceptance.

All registry, Windows handles, Inno compilation/install/uninstall and taskkill
calls are mocked. Only disposable temporary files and Python child processes are
real; no installed software, user data or actual registry is accessed.
"""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from scripts import smoke_windows_installers as runner
from testbox import updater


class WindowsInstallerSmokeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / "smoke"
        self.iscc = self.root / "mock-iscc.exe"
        self.iscc.write_bytes(b"placeholder: never executed")

    def smoke(self):
        return runner.NativeSmoke(output=self.output, dist=self.root / "dist",
                                  iscc=self.iscc, version="1.2.3")

    @contextlib.contextmanager
    def disposable_environment(self, **changes):
        environment = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows", "RUNNER_TEMP": str(self.root),
                       "RUNNER_ENVIRONMENT": "github-hosted"}
        environment.update(changes)
        with patch.dict(os.environ, environment, clear=True), patch.object(runner.sys, "platform", "win32"):
            yield

    @contextlib.contextmanager
    def fake_registry(self):
        registry = types.ModuleType("winreg")
        registry.HKEY_CURRENT_USER = 0x80000001
        registry.KEY_READ = 0x20019
        registry.KEY_WOW64_64KEY = 0x100
        registry.REG_SZ = 1
        registry.REG_EXPAND_SZ = 2
        registry.OpenKey = MagicMock()
        registry.QueryValueEx = MagicMock()
        registry.OpenKey.return_value.__enter__.return_value = registry.OpenKey.return_value
        with patch.dict(sys.modules, {"winreg": registry}):
            yield registry

    def managed_install(self):
        install = self.root / "managed"
        install.mkdir()
        payload = b"same length hashes matter"
        file = install / "nested" / "file.bin"
        file.parent.mkdir()
        file.write_bytes(payload)
        manifest = {"schema_version": updater.MANIFEST_SCHEMA_VERSION, "component": "cli", "version": "1.2.3",
                    "base_version": None, "files": [{"path": "nested/file.bin", "size": len(payload),
                                                        "sha256": hashlib.sha256(payload).hexdigest()}],
                    "changed_files": ["nested/file.bin"], "deleted_files": []}
        (install / updater.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        (install / "TestBox-CLI-Updater.exe").write_bytes(b"synthetic updater; never executed")
        return install, manifest, file

    def invoke_main(self, *, execute=None, cleanup=None, **argument_changes):
        arguments = {"--output-root": str(self.output), "--iscc": str(self.iscc), "--version": "1.2.3"}
        arguments.update(argument_changes)
        argv = ["smoke_windows_installers.py"] + [item for pair in arguments.items() for item in pair]
        stdout = io.StringIO()
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
            with patch.object(runner, "require_disposable_runner", return_value=self.output), \
                 patch.object(runner, "assert_no_existing_installations"), \
                 patch.object(runner.NativeSmoke, "execute", autospec=True, side_effect=execute) as execute_mock, \
                 patch.object(runner.NativeSmoke, "cleanup_owned_installs", autospec=True,
                              return_value=cleanup or []) as cleanup_mock:
                code = runner.main()
        return code, json.loads(stdout.getvalue()), execute_mock, cleanup_mock

    def test_platform_and_ci_guards_refuse_without_creating_output(self):
        for platform, actions, runner_os in (("darwin", "true", "Windows"), ("linux", "true", "Windows"),
                                            ("win32", "false", "Windows"), ("win32", "True", "Windows"),
                                            ("win32", "", "Windows"), ("win32", "true", "Linux")):
            with self.subTest(platform=platform, actions=actions, runner_os=runner_os):
                with patch.dict(os.environ, {"GITHUB_ACTIONS": actions, "RUNNER_OS": runner_os,
                                             "RUNNER_TEMP": str(self.root), "RUNNER_ENVIRONMENT": "github-hosted"}, clear=True), \
                     patch.object(runner.sys, "platform", platform):
                    with self.assertRaisesRegex(RuntimeError, "disposable"):
                        runner.require_disposable_runner(self.output)
                self.assertFalse(self.output.exists())
        for runner_environment in ("self-hosted", "", "GitHub-hosted"):
            with self.subTest(runner_environment=runner_environment), \
                 self.disposable_environment(RUNNER_ENVIRONMENT=runner_environment):
                with self.assertRaisesRegex(RuntimeError, "GitHub-hosted"):
                    runner.require_disposable_runner(self.output)
                self.assertFalse(self.output.exists())
        with self.disposable_environment():
            del os.environ["RUNNER_ENVIRONMENT"]
            with self.assertRaisesRegex(RuntimeError, "GitHub-hosted"):
                runner.require_disposable_runner(self.output)
        with self.disposable_environment(RUNNER_TEMP=""):
            with self.assertRaisesRegex(RuntimeError, "RUNNER_TEMP"):
                runner.require_disposable_runner(self.output)

    def test_output_must_be_new_child_not_existing_root_sibling_or_escape(self):
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "keep.txt"
        marker.write_bytes(b"keep")
        existing_file = self.root / "file"
        existing_file.write_bytes(b"keep-file")
        for output in (self.root, self.root.parent, self.root.parent / (self.root.name + "-sibling"),
                       self.root / ".." / "escaped-smoke", existing, existing_file):
            with self.subTest(output=output), self.disposable_environment():
                with self.assertRaisesRegex(RuntimeError, "new child"):
                    runner.require_disposable_runner(output)
        self.assertEqual(marker.read_bytes(), b"keep")
        self.assertEqual(existing_file.read_bytes(), b"keep-file")
        with self.disposable_environment():
            nested = self.root / "new" / "nested"
            self.assertEqual(runner.require_disposable_runner(nested), nested.resolve())
        self.assertFalse(nested.exists())

    def test_output_symlink_escape_is_rejected_when_symlinks_supported(self):
        with tempfile.TemporaryDirectory() as external:
            link = self.root / "link"
            try:
                link.symlink_to(Path(external), target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Directory symlinks unavailable: {error}")
            with self.disposable_environment(), self.assertRaisesRegex(RuntimeError, "new child"):
                runner.require_disposable_runner(link / "smoke")
            self.assertFalse((Path(external) / "smoke").exists())

    def test_app_ids_match_actual_full_installers_and_uninstall_registry_lookup(self):
        sources = {"cli": "TestBoxCLI.iss", "gui": "TestBox.iss"}
        self.assertEqual(set(runner.APP_IDS), set(sources))
        self.assertEqual(len(set(runner.APP_IDS.values())), 2)
        for component, filename in sources.items():
            with self.subTest(component=component):
                text = (runner.ROOT / "installer" / filename).read_text(encoding="utf-8")
                match = re.search(r"^AppId=\{\{([0-9A-Fa-f-]+)\}\s*$", text, re.M)
                self.assertIsNotNone(match)
                self.assertEqual(runner.APP_IDS[component], match.group(1))
                with self.fake_registry() as registry:
                    self.assertTrue(runner.registry_uninstall_exists(component))
                    registry.OpenKey.assert_called_once_with(
                        registry.HKEY_CURRENT_USER,
                        f"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{{{match.group(1)}}}_is1",
                        0, registry.KEY_READ | registry.KEY_WOW64_64KEY)
                    registry.QueryValueEx.assert_not_called()

    def test_registry_reads_only_hkcu_component_key_in_64_bit_view(self):
        for component in runner.APP_IDS:
            with self.subTest(component=component), self.fake_registry() as registry:
                registry.QueryValueEx.return_value = (str(self.output), registry.REG_SZ)
                self.assertEqual(runner.registry_install_dir(component), str(self.output))
                registry.OpenKey.assert_called_once_with(registry.HKEY_CURRENT_USER,
                    f"Software\\TestBox\\{component.upper()}", 0, registry.KEY_READ | registry.KEY_WOW64_64KEY)
                registry.QueryValueEx.assert_called_once_with(registry.OpenKey.return_value, "InstallDir")

    def test_missing_registry_keys_are_absent_but_access_errors_fail_closed(self):
        for lookup in (runner.registry_install_dir, runner.registry_uninstall_exists):
            with self.subTest(lookup=lookup.__name__), self.fake_registry() as registry:
                registry.OpenKey.side_effect = FileNotFoundError("no key")
                self.assertEqual(lookup("cli"), None if lookup is runner.registry_install_dir else False)
                registry.OpenKey.side_effect = PermissionError("registry access denied")
                with self.assertRaises(PermissionError):
                    lookup("cli")

    def test_invalid_install_dir_types_and_query_access_errors_fail_closed(self):
        for value, kind in ((123, 1), (None, 1), ("path", 2)):
            with self.subTest(value=value, kind=kind), self.fake_registry() as registry:
                registry.QueryValueEx.return_value = value, kind
                with self.assertRaisesRegex(RuntimeError, "Invalid existing"):
                    runner.registry_install_dir("cli")
        with self.fake_registry() as registry:
            registry.QueryValueEx.side_effect = PermissionError("value access denied")
            with self.assertRaises(PermissionError):
                runner.registry_install_dir("cli")

    def test_existing_registry_key_missing_install_dir_must_fail_closed(self):
        # Safety regression: a present product key with a missing value is not an
        # absent key. Do not skip/xfail this assertion to hide a runner defect.
        with self.fake_registry() as registry:
            registry.QueryValueEx.side_effect = FileNotFoundError("InstallDir value missing in existing key")
            with self.assertRaises((RuntimeError, FileNotFoundError)):
                runner.registry_install_dir("cli")

    def test_existing_component_or_uninstall_registration_prevents_start(self):
        for component in runner.APP_IDS:
            for registered_by in ("install-dir", "uninstall-key"):
                with self.subTest(component=component, registered_by=registered_by):
                    directories = lambda c: str(self.root / "foreign") if c == component and registered_by == "install-dir" else None
                    uninstall = lambda c: c == component and registered_by == "uninstall-key"
                    with patch.object(runner, "registry_install_dir", side_effect=directories), \
                         patch.object(runner, "registry_uninstall_exists", side_effect=uninstall):
                        with self.assertRaisesRegex(RuntimeError, f"existing {component}"):
                            runner.assert_no_existing_installations()
        with patch.object(runner, "registry_install_dir", return_value=None), \
             patch.object(runner, "registry_uninstall_exists", return_value=False):
            runner.assert_no_existing_installations()
        for lookup in ("registry_install_dir", "registry_uninstall_exists"):
            with self.subTest(lookup=lookup), \
                 patch.object(runner, "registry_install_dir", return_value=None), \
                 patch.object(runner, "registry_uninstall_exists", return_value=False):
                with patch.object(runner, lookup, side_effect=PermissionError("access denied")):
                    with self.assertRaises(PermissionError):
                        runner.assert_no_existing_installations()

    def test_read_log_decodes_utf16_le_be_utf8_and_boms(self):
        text = "更新失败：组件不匹配\nTEST DATA ONLY\n"
        payloads = (text.encode("utf-8"), text.encode("utf-8-sig"), text.encode("utf-16"),
                    b"\xfe\xff" + text.encode("utf-16-be"))
        log = self.root / "log.txt"
        for data in payloads:
            with self.subTest(prefix=data[:3]):
                log.write_bytes(data)
                self.assertEqual(runner.read_log(log), text)
        log.write_bytes(b"diagnostic\xff")
        self.assertEqual(runner.read_log(log), "diagnostic\ufffd")
        log.write_bytes(b"")
        self.assertEqual(runner.read_log(log), "")

    def test_snapshot_uses_real_hashes_and_detects_same_size_tamper_and_missing_file(self):
        path = self.root / "keep.txt"
        path.write_bytes(b"original")
        snapshot = runner.snapshot_files([path])
        self.assertEqual(snapshot, {str(path): hashlib.sha256(b"original").hexdigest()})
        runner.assert_snapshot(snapshot)
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(AssertionError, "changed or disappeared"):
            runner.assert_snapshot(snapshot)
        path.unlink()
        with self.assertRaisesRegex(AssertionError, "changed or disappeared"):
            runner.assert_snapshot(snapshot)
        with self.assertRaisesRegex(AssertionError, "Missing preserved"):
            runner.snapshot_files([path])
        with self.assertRaises(AssertionError):
            runner.snapshot_files([self.root])

    def test_verify_managed_validates_actual_content_identity_and_updater_snapshot(self):
        install, manifest, file = self.managed_install()
        snapshot = runner.verify_managed(install, "cli", "1.2.3")
        self.assertEqual(set(snapshot), {str(file), str(install / updater.MANIFEST_NAME),
                                       str(install / "TestBox-CLI-Updater.exe")})
        for component, version in (("gui", "1.2.3"), ("cli", "9.9.9")):
            with self.subTest(component=component, version=version), self.assertRaisesRegex(AssertionError, "identity"):
                runner.verify_managed(install, component, version)
        payload = file.read_bytes()
        for tampered in (bytes([payload[0] ^ 1]) + payload[1:], payload + b"x"):
            file.write_bytes(tampered)
            with self.assertRaisesRegex(AssertionError, "managed file mismatch"):
                runner.verify_managed(install, "cli", "1.2.3")
        file.unlink()
        with self.assertRaisesRegex(AssertionError, "managed file mismatch"):
            runner.verify_managed(install, "cli", "1.2.3")
        file.write_bytes(payload)
        updater_exe = install / "TestBox-CLI-Updater.exe"
        updater_exe.write_bytes(b"tampered updater")
        with self.assertRaisesRegex(AssertionError, "changed or disappeared"):
            runner.assert_snapshot(snapshot)
        updater_exe.unlink()
        with self.assertRaisesRegex(AssertionError, "Missing preserved"):
            runner.verify_managed(install, "cli", "1.2.3")
        manifest["files"][0]["path"] = "../escape"
        (install / updater.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            runner.installed_manifest(install)

    def test_setup_and_uninstall_flags_and_expected_exit_seven(self):
        smoke = self.smoke()
        executable = self.root / "never-run-installer.exe"
        directory = self.root / "Unicode 安装 path"
        for uninstall in (False, True):
            with self.subTest(uninstall=uninstall):
                name = f"mock-setup-{uninstall}"
                log = smoke.logs / f"{name}-inno.log"
                def fake_run(exe, arguments, process_name, **kwargs):
                    log.write_text("mocked Inno diagnostic", encoding="utf-8")
                with patch.object(smoke, "run", side_effect=fake_run) as run:
                    self.assertEqual(smoke.setup(executable, name, directory=directory,
                                                 expected=(7,), uninstall=uninstall), log)
                expected = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}"]
                if not uninstall:
                    expected += ["/SP-", "/NOCANCEL", "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS",
                                 "/RESTARTEXITCODE=3010"]
                expected.append(f"/DIR={directory}")
                run.assert_called_once_with(executable, expected, name, expected=(7,))
        with patch.object(smoke, "run"), self.assertRaisesRegex(AssertionError, "preserve its log"):
            smoke.setup(executable, "missing-log")

    def test_setup_failure_surfaces_encoded_inno_diagnostic(self):
        smoke = self.smoke()
        for encoding in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16-be'):
            with self.subTest(encoding=encoding):
                name = f"synthetic-failure-{encoding}"
                log = smoke.logs / f"{name}-inno.log"
                def failing_run(*args, **kwargs):
                    data = "PrepareToInstall failed: 安装目录包含无效字符。".encode(encoding)
                    log.write_bytes((b"\xfe\xff" if encoding == 'utf-16-be' else b"") + data)
                    raise RuntimeError("Unexpected exit 7; empty stdout")
                with patch.object(smoke, "run", side_effect=failing_run):
                    with self.assertRaisesRegex(RuntimeError, "Unexpected exit 7") as caught:
                        smoke.setup(self.root / "not-executed.exe", name)
                self.assertIn("PrepareToInstall failed: 安装目录包含无效字符。", str(caught.exception))
                self.assertIn(str(log), str(caught.exception))
                self.assertIsInstance(caught.exception.__cause__, RuntimeError)
                self.assertEqual(smoke.summary["checks"], [])

    def test_setup_failure_bounds_diagnostic_tail(self):
        smoke = self.smoke()
        log = smoke.logs / "bounded-inno.log"
        def failing_run(*args, **kwargs):
            log.write_text("OLD LOG CONTENT\n" + "x" * 5000 + "\n安装失败", encoding="utf-8")
            raise RuntimeError("Unexpected exit 7")
        with patch.object(smoke, "run", side_effect=failing_run):
            with self.assertRaisesRegex(RuntimeError, "Unexpected exit 7") as caught:
                smoke.setup(self.root / "not-executed.exe", "bounded")
        message = str(caught.exception)
        self.assertNotIn("OLD LOG CONTENT", message)
        self.assertTrue(message.endswith("安装失败"))
        self.assertLess(len(message), 4300)

    def test_setup_failure_handles_missing_empty_and_unreadable_inno_logs(self):
        smoke = self.smoke()
        for kind in ('missing', 'empty', 'unreadable'):
            with self.subTest(kind=kind):
                name = f"synthetic-{kind}"
                log = smoke.logs / f"{name}-inno.log"
                if kind != 'missing':
                    log.write_bytes(b"")
                error = RuntimeError("Unexpected exit 7")
                with patch.object(smoke, "run", side_effect=error):
                    if kind == 'unreadable':
                        with patch.object(runner, "read_log", side_effect=PermissionError("cannot read log")):
                            with self.assertRaisesRegex(RuntimeError, "Unexpected exit 7") as caught:
                                smoke.setup(self.root / "not-executed.exe", name)
                        self.assertIn("Cannot read Inno log", str(caught.exception))
                        self.assertIs(caught.exception.__cause__, error)
                    else:
                        with self.assertRaises(RuntimeError) as caught:
                            smoke.setup(self.root / "not-executed.exe", name)
                        self.assertIs(caught.exception, error)

    def test_setup_timeout_keeps_exception_type_and_inno_log(self):
        smoke = self.smoke()
        def timeout_run(*args, **kwargs):
            (smoke.logs / "timeout-inno.log").write_text("安装超时诊断", encoding="utf-16")
            raise TimeoutError("Native smoke process timed out")
        with patch.object(smoke, "run", side_effect=timeout_run):
            with self.assertRaisesRegex(TimeoutError, "timed out") as caught:
                smoke.setup(self.root / "not-executed.exe", "timeout")
        self.assertIn("安装超时诊断", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, TimeoutError)

    def test_main_setup_failure_includes_real_child_inno_log_in_summary(self):
        # Use a real Python child for exit/log handling, not a Windows installer.
        original_run = runner.NativeSmoke.run
        def child_run(smoke, executable, arguments, name, *, expected=(0,), timeout=300):
            log = smoke.logs / f"{name}-inno.log"
            program = ("from pathlib import Path; import sys; "
                       f"Path({str(log)!r}).write_text('PrepareToInstall failed: 安装目录包含无效字符。', encoding='utf-16'); "
                       "sys.exit(7)")
            return original_run(smoke, Path(sys.executable), ['-c', program], name,
                                expected=expected, timeout=30)
        def execute(smoke):
            smoke.setup(self.root / "not-executed.exe", "install-cli")
        with patch.object(runner.NativeSmoke, "run", new=child_run):
            code, summary, executed, cleaned = self.invoke_main(execute=execute)
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "failed")
        self.assertIn("Unexpected exit 7", summary["message"])
        self.assertIn("PrepareToInstall failed: 安装目录包含无效字符。", summary["message"])
        self.assertEqual(summary["processes"][0]["exit_code"], 7)
        self.assertEqual(json.loads((self.output / "summary.json").read_text(encoding='utf-8')), summary)
        self.assertEqual(executed.call_count, 1)
        cleaned.assert_called_once_with(executed.call_args.args[0])

    def test_compile_delta_uses_component_source_and_output_compiled_not_release_dist(self):
        smoke = self.smoke()
        package = self.root / "dist" / "installer-smoke-test.zip"
        for component, source in (("cli", "TestBoxCLIUpdate.iss"), ("gui", "TestBoxUpdate.iss")):
            with self.subTest(component=component):
                name = f"mock-{component}-delta"
                expected = smoke.output / "compiled" / f"{name}.exe"
                def fake_run(executable, arguments, process_name):
                    # Emulate compiler output only; no ISCC process is launched.
                    expected.write_bytes(b"synthetic compiler output; never execute")
                with patch.object(smoke, "run", side_effect=fake_run) as run:
                    self.assertEqual(smoke.compile_delta(component, package, name), expected)
                run.assert_called_once_with(smoke.iscc, ["/DAppVersion=1.2.3",
                    f"/DUpdatePackage={package.name}", f"/O{smoke.output / 'compiled'}", f"/F{name}",
                    str(runner.ROOT / "installer" / source)], f"compile-{name}")
                self.assertTrue(expected.is_file())
                self.assertFalse(smoke.dist.exists())
        with patch.object(smoke, "run"), self.assertRaisesRegex(AssertionError, "Compiler output missing"):
            smoke.compile_delta("cli", package, "missing-compiled-output")

    def test_failure_requires_exit_seven_diagnostic_and_unchanged_snapshots(self):
        smoke = self.smoke()
        protected = self.root / "protected.txt"
        protected.write_bytes(b"user file")
        smoke.protected = runner.snapshot_files([protected])
        baseline = dict(smoke.protected)
        log = smoke.logs / "mock-inno.log"
        log.write_text("更新包基于错误版本", encoding="utf-16")
        executable = self.root / "mock-setup.exe"
        with patch.object(smoke, "setup", return_value=log) as setup, \
             patch.object(smoke, "assert_registration") as registration:
            smoke.failure(executable, "wrong-base", "cli", baseline, token="更新包基于")
            setup.assert_called_once_with(executable, "wrong-base", expected=(7,))
            registration.assert_called_once_with("cli")
            self.assertEqual(smoke.summary["checks"], [{"name": "wrong-base", "status": "passed"}])
            with self.assertRaisesRegex(AssertionError, "expected updater diagnostic"):
                smoke.failure(executable, "wrong-token", "cli", baseline, token="not present")
            protected.write_bytes(b"tampered")
            with self.assertRaisesRegex(AssertionError, "changed or disappeared"):
                smoke.failure(executable, "tampered", "cli", baseline, token="更新包基于")
            self.assertEqual(len(smoke.summary["checks"]), 1)

    def test_run_real_python_child_checks_exit_code_and_preserves_readable_logs(self):
        smoke = self.smoke()
        executable = Path(sys.executable)
        program = "import sys; sys.stdout.buffer.write('真实子进程诊断\\n'.encode('utf-8')); sys.stdout.flush(); sys.exit(7)"
        log = smoke.run(executable, ["-c", program], "expected-seven", expected=(7,), timeout=30)
        self.assertIn("真实子进程诊断", runner.read_log(log))
        with self.assertRaisesRegex(RuntimeError, "Unexpected exit 7.*unexpected-seven"):
            smoke.run(executable, ["-c", program], "unexpected-seven", timeout=30)
        self.assertEqual([p["exit_code"] for p in smoke.summary["processes"]], [7, 7])
        for process in smoke.summary["processes"]:
            self.assertTrue(Path(process["log"]).is_file())
            self.assertIn("真实子进程诊断", runner.read_log(Path(process["log"])))

    def test_run_timeout_kills_only_owned_pid_tree_with_mocked_windows_tools(self):
        smoke = self.smoke()
        process = MagicMock(pid=43210)
        process.wait.side_effect = [subprocess.TimeoutExpired("mock.exe", 1), 1]
        with patch.object(runner.subprocess, "Popen", return_value=process) as popen, \
             patch.object(runner.subprocess, "run") as taskkill:
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                smoke.run(Path("mock.exe"), ["argument"], "timeout", timeout=1)
        self.assertEqual(popen.call_args.args[0], ["mock.exe", "argument"])
        self.assertEqual(popen.call_args.kwargs["cwd"], smoke.output)
        self.assertEqual(taskkill.call_args.args[0], ["taskkill", "/PID", "43210", "/T", "/F"])
        self.assertFalse(taskkill.call_args.kwargs["check"])
        self.assertEqual(process.wait.call_args_list, [call(timeout=1), call(timeout=30)])
        self.assertTrue((smoke.logs / "timeout-process.log").is_file())

    def test_uninstall_requires_owned_registration_and_verifies_removal(self):
        smoke = self.smoke()
        protected = self.root / "sentinel"
        protected.write_bytes(b"preserved")
        smoke.protected = runner.snapshot_files([protected])
        with patch.object(runner, "registry_install_dir", side_effect=[str(smoke.installs["cli"]), None]), \
             patch.object(runner, "registry_uninstall_exists", return_value=False), \
             patch.object(smoke, "setup") as setup:
            smoke.uninstall("cli", "uninstall-cli")
            setup.assert_called_once_with(smoke.installs["cli"] / "unins000.exe", "uninstall-cli", uninstall=True)
        for actual in (None, str(self.root / "foreign")):
            with self.subTest(actual=actual), patch.object(runner, "registry_install_dir", return_value=actual), \
                 patch.object(smoke, "setup") as setup:
                with self.assertRaisesRegex(AssertionError, "registry installation path"):
                    smoke.uninstall("cli", "refuse")
                setup.assert_not_called()
        with patch.object(runner, "registry_install_dir", side_effect=[str(smoke.installs["cli"]), None]), \
             patch.object(runner, "registry_uninstall_exists", return_value=True), patch.object(smoke, "setup"):
            with self.assertRaisesRegex(AssertionError, "active cli registration"):
                smoke.uninstall("cli", "registry-remains")
        smoke.installs["cli"].mkdir(parents=True)
        (smoke.installs["cli"] / updater.MANIFEST_NAME).write_text("leftover", encoding="utf-8")
        with patch.object(runner, "registry_install_dir", side_effect=[str(smoke.installs["cli"]), None]), \
             patch.object(runner, "registry_uninstall_exists", return_value=False), patch.object(smoke, "setup"):
            with self.assertRaisesRegex(AssertionError, "program files"):
                smoke.uninstall("cli", "files-remain")

    def test_cleanup_never_uninstalls_unexpected_registry_path_and_continues_owned_component(self):
        smoke = self.smoke()
        foreign = self.root / "foreign installation"
        foreign.mkdir()
        sentinel = foreign / "unins000.exe"
        sentinel.write_bytes(b"unknown software must never execute")
        paths = {"cli": str(foreign), "gui": str(smoke.installs["gui"])}
        with patch.object(runner, "registry_install_dir", side_effect=lambda c: paths[c]), \
             patch.object(smoke, "uninstall") as uninstall:
            errors = smoke.cleanup_owned_installs()
        uninstall.assert_called_once_with("gui", "cleanup-gui")
        self.assertEqual(len(errors), 1)
        self.assertIn("cli: Refusing cleanup of unexpected registered path", errors[0])
        self.assertEqual(sentinel.read_bytes(), b"unknown software must never execute")

    def test_cleanup_skips_absent_and_reports_registry_or_uninstall_failures(self):
        smoke = self.smoke()
        with patch.object(runner, "registry_install_dir", return_value=None), patch.object(smoke, "uninstall") as uninstall:
            self.assertEqual(smoke.cleanup_owned_installs(), [])
            uninstall.assert_not_called()
        def lookup(component):
            if component == "cli":
                raise PermissionError("cannot read registry")
            return str(smoke.installs[component])
        with patch.object(runner, "registry_install_dir", side_effect=lookup), \
             patch.object(smoke, "uninstall", side_effect=RuntimeError("cleanup failed")) as uninstall:
            errors = smoke.cleanup_owned_installs()
        uninstall.assert_called_once_with("gui", "cleanup-gui")
        self.assertEqual(errors, ["cli: cannot read registry", "gui: cleanup failed"])

    def test_file_lock_helper_uses_mocked_read_share_handle_and_closes_on_error(self):
        kernel = MagicMock()
        kernel.CreateFileW.return_value = 42
        with patch.object(runner.ctypes, "WinDLL", create=True, return_value=kernel) as windll:
            with self.assertRaisesRegex(RuntimeError, "inside context"):
                with runner.hold_without_delete_share(self.root / "locked"):
                    kernel.CloseHandle.assert_not_called()
                    raise RuntimeError("inside context")
        windll.assert_called_once_with("kernel32", use_last_error=True)
        kernel.CreateFileW.assert_called_once_with(str(self.root / "locked"), 0x80000000, 0x1, None, 3, 0x80, None)
        kernel.CloseHandle.assert_called_once_with(42)
        kernel.reset_mock()
        kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
        with patch.object(runner.ctypes, "WinDLL", create=True, return_value=kernel), \
             patch.object(runner.ctypes, "get_last_error", create=True, return_value=5), \
             patch.object(runner.ctypes, "WinError", create=True, return_value=OSError("denied")):
            with self.assertRaisesRegex(OSError, "denied"), runner.hold_without_delete_share(self.root / "locked"):
                self.fail("invalid handle must not enter context")
        kernel.CloseHandle.assert_not_called()

    def test_main_preflight_refusals_do_not_construct_runner_or_create_output(self):
        cases = ({"guard": RuntimeError("not disposable")}, {"existing": RuntimeError("existing installation")},
                 {"iscc": str(self.root / "missing-iscc")}, {"version": "1.2.3+smoke"})
        for case in cases:
            with self.subTest(case=case):
                argv = ["smoke", "--output-root", str(self.output), "--iscc", case.get("iscc", str(self.iscc)),
                        "--version", case.get("version", "1.2.3")]
                stdout = io.StringIO()
                with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout), \
                     patch.object(runner, "require_disposable_runner", return_value=self.output,
                                  side_effect=case.get("guard")), \
                     patch.object(runner, "assert_no_existing_installations", side_effect=case.get("existing")), \
                     patch.object(runner, "NativeSmoke") as constructor:
                    self.assertEqual(runner.main(), 2)
                self.assertEqual(json.loads(stdout.getvalue())["status"], "refused")
                constructor.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_main_process_failure_and_cleanup_failure_write_failed_summary_and_readable_logs(self):
        def execute(smoke):
            (smoke.logs / "mocked-inno.log").write_text("安装器失败日志", encoding="utf-16")
            smoke.run(Path(sys.executable), ["-c", "import sys; print('child exit diagnostic'); sys.exit(7)"],
                      "failed-child", timeout=30)
        code, summary, executed, cleaned = self.invoke_main(execute=execute, cleanup=["cli: cleanup failed"])
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "failed")
        self.assertIn("Unexpected exit 7", summary["message"])
        self.assertIn("child exit diagnostic", summary["message"])
        self.assertEqual(summary["cleanup_errors"], ["cli: cleanup failed"])
        self.assertEqual(json.loads((self.output / "summary.json").read_text(encoding="utf-8")), summary)
        self.assertEqual(summary["processes"][0]["exit_code"], 7)
        self.assertIn("child exit diagnostic", runner.read_log(Path(summary["processes"][0]["log"])))
        self.assertEqual(runner.read_log(self.output / "logs" / "mocked-inno.log"), "安装器失败日志")
        self.assertEqual(executed.call_count, 1)
        cleaned.assert_called_once_with(executed.call_args.args[0])

    def test_main_cleanup_only_failure_overrides_success_and_persists_summary(self):
        def execute(smoke):
            smoke.record("mocked acceptance step")
            (smoke.logs / "kept.log").write_text("readable log", encoding="utf-8")
        code, summary, _, _ = self.invoke_main(execute=execute, cleanup=["gui: uninstall rejected"])
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["cleanup_errors"], ["gui: uninstall rejected"])
        self.assertEqual(summary["checks"], [{"name": "mocked acceptance step", "status": "passed"}])
        self.assertEqual(json.loads((self.output / "summary.json").read_bytes()), summary)
        self.assertEqual(runner.read_log(self.output / "logs" / "kept.log"), "readable log")

    def test_main_mocked_success_returns_zero_and_records_explicit_synthetic_scope(self):
        code, summary, _, cleanup = self.invoke_main()
        self.assertEqual(code, 0)
        self.assertEqual(summary["status"], "passed")
        self.assertIn("synthetic", summary["delta_kind"])
        self.assertIn("not adjacent-release compatibility", summary["delta_kind"])
        self.assertEqual(json.loads((self.output / "summary.json").read_bytes()), summary)
        self.assertEqual(cleanup.call_count, 1)


if __name__ == "__main__":
    unittest.main()
