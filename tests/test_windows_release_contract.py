"""Executable contracts for Windows release sources; not Windows installer tests."""
from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts import testbox_updater
from testbox import updater

ROOT = Path(__file__).resolve().parents[1]


def section(source: str, name: str) -> str:
    match = re.search(r'^\[' + re.escape(name) + r'\]\s*\n(.*?)(?=^\[|\Z)', source, re.M | re.S)
    return match.group(1) if match else ''


class WindowsReleaseContractTests(unittest.TestCase):
    def test_component_installation_roots_registry_and_app_ids_are_separate(self):
        app_ids = []
        for channel, name in [('CLI', 'TestBoxCLI.iss'), ('GUI', 'TestBox.iss')]:
            source = (ROOT / 'installer' / name).read_text(encoding='utf-8')
            setup = section(source, 'Setup')
            self.assertIn(f'DefaultDirName={{localappdata}}\\Programs\\TestBox {channel}', setup)
            self.assertIn('PrivilegesRequired=lowest', setup)
            self.assertIn(f'OutputBaseFilename=TestBox-{channel}-Install-v{{#AppVersion}}', setup)
            self.assertIn(f'Subkey: "Software\\TestBox\\{channel}"', section(source, 'Registry'))
            self.assertIn(f'..\\dist\\windows\\{channel.lower()}\\*', section(source, 'Files'))
            app_ids.extend(re.findall(r'^AppId=(.+)$', setup, re.M))
        self.assertEqual(len(set(app_ids)), 2)

    def test_inno_pascal_cross_component_paths_have_closed_string_literals(self):
        expected = {
            "TestBoxCLI.iss": "    OtherDir := ExpandConstant('{localappdata}\\Programs\\TestBox GUI');",
            "TestBoxCLIUpdate.iss": "    OtherDir := ExpandConstant('{localappdata}\\Programs\\TestBox GUI');",
            "TestBox.iss": "    OtherDir := ExpandConstant('{localappdata}\\Programs\\TestBox CLI');",
            "TestBoxUpdate.iss": "    OtherDir := ExpandConstant('{localappdata}\\Programs\\TestBox CLI');",
        }
        for filename, exact_line in expected.items():
            with self.subTest(filename=filename):
                lines = (ROOT / 'installer' / filename).read_text(encoding='utf-8').splitlines()
                matching = [line for line in lines if 'OtherDir := ExpandConstant' in line]
                self.assertEqual(matching, [exact_line])
                self.assertNotIn(exact_line[:-1], matching)
                self.assertEqual(exact_line.count("'") % 2, 0)

    def test_install_and_uninstall_do_not_recursively_delete_user_files(self):
        for name in ('TestBox.iss', 'TestBoxCLI.iss'):
            source = (ROOT / 'installer' / name).read_text(encoding='utf-8')
            with self.subTest(name=name):
                self.assertEqual(section(source, 'InstallDelete').strip(), '')
                self.assertEqual(section(source, 'UninstallDelete').strip(), '')
                self.assertNotIn('uninsdeletekey', section(source, 'Registry'))
                code = section(source, 'Code')
                self.assertIn('UninstallLogMode=overwrite', section(source, 'Setup'))
                self.assertIn('PathsOverlap', code)
                self.assertIn('function InitializeUninstall(): Boolean;', code)
                self.assertIn("InstallationPathError(ExpandConstant('{app}'))", code)
                self.assertIn("Result := ErrorMessage = '';", code)
                self.assertIn("ExpandConstant('{localappdata}\\TestBox')", code)
                self.assertIn('WizardDirValue', code)
                self.assertNotRegex(code, r'(?i)\b(?:DelTree|RemoveDir)\s*\(')

    def test_incremental_installer_checks_exit_code_and_surfaces_utf8_error(self):
        for channel, name in [('CLI', 'TestBoxCLIUpdate.iss'), ('GUI', 'TestBoxUpdate.iss')]:
            source = (ROOT / 'installer' / name).read_text(encoding='utf-8')
            with self.subTest(channel=channel):
                self.assertIn('Uninstallable=no', section(source, 'Setup'))
                self.assertIn('CreateUninstallRegKey=no', section(source, 'Setup'))
                self.assertEqual(section(source, 'Run'), '')
                self.assertEqual(section(source, 'Registry'), '')
                files = section(source, 'Files')
                self.assertIn(f'..\\dist\\windows\\{channel.lower()}\\TestBox-{channel}-Updater.exe', files)
                self.assertEqual(files.count('Flags: dontcopy'), 2)
                code = section(source, 'Code')
                self.assertIn('function PrepareToInstall', code)
                self.assertIn('ewWaitUntilTerminated, ResultCode', code)
                self.assertIn('ResultCode <> 0', code)
                self.assertIn('SysErrorMessage(ResultCode)', code)
                self.assertIn('UTF8Decode(Diagnostic)', code)
                self.assertIn('--result-file', code)
                self.assertIn('if UpdateApplied then exit', code)
                self.assertIn('UpdateApplied := True', code)
                self.assertIn("'update-manifest.json'", code)

    def test_inno_code_literals_comments_and_parentheses_are_balanced(self):
        # A lexical check only, deliberately not described as Pascal compilation.
        for filename in ('TestBox.iss', 'TestBoxCLI.iss', 'TestBoxUpdate.iss', 'TestBoxCLIUpdate.iss'):
            with self.subTest(filename=filename):
                code = section((ROOT / 'installer' / filename).read_text(encoding='utf-8'), 'Code')
                quoted = comment = False
                parentheses = 0
                index = 0
                while index < len(code):
                    char = code[index]
                    if comment:
                        if char == '}':
                            comment = False
                    elif quoted:
                        self.assertNotEqual(char, '\n', 'Pascal string must close before the next line')
                        if char == "'":
                            if index + 1 < len(code) and code[index + 1] == "'":
                                index += 1
                            else:
                                quoted = False
                    elif char == '{':
                        comment = True
                    elif char == "'":
                        quoted = True
                    elif char == '(':
                        parentheses += 1
                    elif char == ')':
                        parentheses -= 1
                        self.assertGreaterEqual(parentheses, 0)
                    index += 1
                self.assertFalse(quoted)
                self.assertFalse(comment)
                self.assertEqual(parentheses, 0)

    def test_all_four_scripts_share_fail_closed_component_path_guard(self):
        guards = []
        for channel, filename in [('CLI', 'TestBoxCLI.iss'), ('CLI', 'TestBoxCLIUpdate.iss'),
                                  ('GUI', 'TestBox.iss'), ('GUI', 'TestBoxUpdate.iss')]:
            with self.subTest(filename=filename):
                code = section((ROOT / 'installer' / filename).read_text(encoding='utf-8'), 'Code')
                other = 'GUI' if channel == 'CLI' else 'CLI'
                guard = code[code.index('function CanonicalPath'):]
                guard = guard[:guard.index('function Initialize') if 'Update' in filename
                              else guard.index('function PrepareToInstall')].strip()
                guards.append(guard.replace(other, 'OTHER'))
                # Equal paths and either ancestor direction, not a raw sibling prefix.
                overlap = guard[guard.index('function PathsOverlap'):guard.index('function InstallationPathError')]
                for side in ('LeftPath', 'RightPath'):
                    self.assertIn(f'{side} := AddBackslash(Lowercase(CanonicalPath({side})));', overlap)
                self.assertIn('(Pos(LeftPath, RightPath) = 1) or (Pos(RightPath, LeftPath) = 1)', overlap)
                self.assertIn("if Trim(InstallDir) = '' then", guard)
                self.assertIn("if Name = '' then RaiseException", guard)
                self.assertIn('RemoveBackslashUnlessRoot(ExtractFileDir(InstallDir)) = InstallDir', guard)
                self.assertIn("Name[Length(Name)] <> '.'", guard)
                self.assertIn("Name[Length(Name)] <> ' '", guard)
                self.assertIn('FindFirst(Path, FindRec)', guard)
                self.assertIn('Name := FindRec.Name;', guard)
                self.assertRegex(guard, r'(?s)FILE_ATTRIBUTE_REPARSE_POINT.*?RaiseException.*?finally\s+FindClose')
                self.assertIn("ExpandConstant('{localappdata}\\TestBox')", guard)
                self.assertIn('PathsOverlap(InstallDir, OtherDir)', guard)
                self.assertIn('PathsOverlap(InstallDir, RegisteredOtherDir)', guard)
                self.assertIn("if Trim(RegisteredOtherDir) <> '' then", guard)
                self.assertIn(f"'Software\\TestBox\\{other}'", guard)
                self.assertIn('except\n    Result := GetExceptionMessage;', guard)
                self.assertLess(guard.index('PathsOverlap(InstallDir, OtherDir)'),
                                guard.index('RegQueryStringValue'))
        self.assertEqual(len(set(guards)), 1, 'Guard drift between full/incremental and CLI/GUI')

    def test_incremental_path_revalidation_happens_before_extraction_and_exec(self):
        for filename in ('TestBoxCLIUpdate.iss', 'TestBoxUpdate.iss'):
            with self.subTest(filename=filename):
                code = section((ROOT / 'installer' / filename).read_text(encoding='utf-8'), 'Code')
                initialize = code[code.index('function InitializeSetup'):code.index('function PrepareToInstall')]
                self.assertIn('TestBoxInstallDir := RegisteredInstallDir;', initialize)
                self.assertLess(initialize.index('InstallationPathError(TestBoxInstallDir)'),
                                initialize.index('FileExists('))
                self.assertIn('Result := False;', initialize)
                self.assertIn('TestBoxInstallDir := RemoveBackslashUnlessRoot(ExpandFileName(TestBoxInstallDir));', initialize)
                prepare = code[code.index('function PrepareToInstall'):]
                self.assertRegex(prepare, r"(?s)Result := InstallationPathError\(TestBoxInstallDir\);.*?if Result <> '' then begin.*?exit;")
                self.assertLess(prepare.index('InstallationPathError'), prepare.index('ExtractTemporaryFile'))
                self.assertLess(prepare.index('InstallationPathError'), prepare.index('Exec('))
                self.assertRegex(prepare, r'(?s)if FileExists\(DiagnosticPath\) then.*?if not DeleteFile\(DiagnosticPath\) then.*?RaiseException')
                self.assertLess(prepare.index('DeleteFile(DiagnosticPath)'), prepare.index('Exec('))
                self.assertIn("if Diagnostic <> '' then", prepare)
                self.assertIn('无法读取更新诊断', prepare)
                self.assertIn('更新器生成了空诊断', prepare)
                self.assertIn('except\n    Result :=', prepare)
                self.assertIn("if Result <> '' then Log(Result);", prepare)
                self.assertRegex(prepare, r'else\s+UpdateApplied := True;')
                self.assertEqual(prepare.count('UpdateApplied := True;'), 1)

    def test_ci_windows_smoke_targets_clean_wheel_with_both_extras(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/build.yml').read_text(encoding='utf-8'))
        windows = workflow['jobs']['windows']
        self.assertEqual(windows['runs-on'], 'windows-latest')
        smokes = [step for step in windows['steps'] if 'smoke_installed.py' in step.get('run', '')]
        self.assertEqual(len(smokes), 1)
        smoke = smokes[0]
        self.assertEqual(smoke['shell'], 'pwsh')
        run = smoke['run']
        for command in ('python -m pip wheel --no-deps . --wheel-dir $wheelDir',
                        'python -m venv $venvDir', 'Scripts\\python.exe',
                        '& $cleanPython -m pip install "$($wheels[0].FullName)[desktop,evidence]"',
                        'python scripts/smoke_installed.py --python $cleanPython --gui --evidence'):
            self.assertIn(command, run)
        self.assertNotIn('--system-site-packages', run)
        self.assertEqual(run.count('if ($LASTEXITCODE -ne 0)'), 4)
        self.assertIn('if ($wheels.Count -ne 1)', run)
        # Preserve the existing package-job smoke rather than adding another publisher.
        package_smokes = [step for step in workflow['jobs']['package']['steps']
                          if 'smoke_installed.py' in step.get('run', '')]
        self.assertEqual(len(package_smokes), 1)
        self.assertIn('[evidence]', package_smokes[0]['run'])
        self.assertEqual(sum(step.get('uses', '').startswith('softprops/action-gh-release@')
                             for job in workflow['jobs'].values() for step in job.get('steps', [])), 1)

    def test_smoke_script_cli_accepts_installed_python_gui_and_evidence(self):
        completed = subprocess.run([sys.executable, str(ROOT / 'scripts/smoke_installed.py'), '--help'],
                                   cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for option in ('--python', '--gui', '--evidence'):
            self.assertIn(option, completed.stdout)

    def test_read_only_build_contract_keeps_cli_qt_free_and_gui_windowed(self):
        source = (ROOT / 'scripts/build_windows.ps1').read_text(encoding='utf-8')
        self.assertIn('--exclude-module PySide6 --exclude-module testbox.gui', source)
        self.assertIn('--onedir --windowed --name TestBox-GUI', source)
        self.assertIn('--onedir --name TestBox ', source)
        self.assertIn('--onefile --name TestBox-CLI-Updater', source)
        self.assertIn('--onefile --name TestBox-GUI-Updater', source)
        for channel in ('CLI', 'GUI'):
            self.assertIn(f'TestBox-{channel}-Updater.exe', source)
        self.assertNotIn('"--collect-all", "PySide6"', source)

    def test_read_only_ci_contract_builds_matching_component_manifests(self):
        source = (ROOT / '.github/workflows/build.yml').read_text(encoding='utf-8')
        self.assertIn("foreach ($component in @('cli', 'gui'))", source)
        self.assertIn('build_update_package.py --component $component', source)
        self.assertIn('dist/windows/$component/update-manifest.json', source)
        for name in ('TestBoxCLI.iss', 'TestBoxCLIUpdate.iss', 'TestBox.iss', 'TestBoxUpdate.iss'):
            self.assertIn('installer/' + name, source)
        for channel in ('CLI', 'GUI'):
            self.assertIn(f'TestBox-{channel}-update-manifest.json', source)

    def test_frozen_updater_identity_matches_exact_component_filename(self):
        for channel in ('CLI', 'GUI'):
            with self.subTest(channel=channel), patch.object(sys, 'frozen', True, create=True), \
                 patch.object(sys, 'executable', f'/install/TestBox-{channel}-Updater.exe'):
                self.assertEqual(testbox_updater.updater_component(), channel.lower())
                self.assertTrue(testbox_updater.default_manifest_url().endswith(f'TestBox-{channel}-update-manifest.json'))
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', '/install/TestBox-Updater.exe'):
            with self.assertRaisesRegex(ValueError, '无法识别'):
                testbox_updater.updater_component()

    def test_frozen_updater_passes_identity_even_with_explicit_package(self):
        for channel in ('CLI', 'GUI'):
            with self.subTest(channel=channel), patch.object(sys, 'frozen', True, create=True), \
                 patch.object(sys, 'executable', f'/install/TestBox-{channel}-Updater.exe'), \
                 patch.object(sys, 'argv', ['updater', '--package', 'local.zip', '--install-dir', '/install']), \
                 patch.object(testbox_updater, 'apply_update', return_value={'version': '1.0.1'}) as mocked, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(testbox_updater.main(), 0)
                self.assertEqual(mocked.call_args.kwargs['expected_component'], channel.lower())

    def test_frozen_updater_passes_identity_even_with_explicit_manifest_url(self):
        with patch.object(sys, 'frozen', True, create=True), \
             patch.object(sys, 'executable', '/install/TestBox-CLI-Updater.exe'), \
             patch.object(sys, 'argv', ['updater', '--manifest-url', 'https://example.test/gui.json']), \
             patch.object(testbox_updater, 'download_and_apply', return_value={'status': 'up_to_date'}) as mocked, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(testbox_updater.main(), 0)
            self.assertEqual(mocked.call_args.kwargs['expected_component'], 'cli')

    def test_updater_script_is_executable_and_writes_failure_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = root / 'result.json'
            completed = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/testbox_updater.py'), '--package', str(root / 'missing.zip'),
                 '--install-dir', str(root / 'install'), '--result-file', str(result)],
                cwd=root, capture_output=True, text=True, timeout=20)
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(json.loads(completed.stdout)['status'], 'failed')
            self.assertEqual(json.loads(result.read_text(encoding='utf-8')), json.loads(completed.stdout))
            self.assertFalse((root / 'install').exists())

    def test_two_component_updates_preserve_other_component_and_shared_user_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / 'localappdata/TestBox'
            data.mkdir(parents=True)
            (data / 'history.sqlite').write_bytes(b'user history')
            (data / 'config.yaml').write_text('user config', encoding='utf-8')
            for channel in ('cli', 'gui'):
                release = root / f'release-{channel}'
                release.mkdir()
                (release / 'app.exe').write_bytes(f'{channel}-v1'.encode())
                updater_name = f'TestBox-{channel.upper()}-Updater.exe'
                (release / updater_name).write_bytes(b'bootstrap')
                package = root / f'{channel}.zip'
                manifest_path = root / f'{channel}.json'
                updater.create_update_package(release, version='1.0.0', component=channel,
                                              output=package, manifest_output=manifest_path)
                install = root / f'localappdata/Programs/TestBox {channel.upper()}'
                updater.apply_update(install, package, expected_component=channel)
                (install / updater_name).write_bytes(b'installed bootstrap')
                (release / 'app.exe').write_bytes(f'{channel}-v2'.encode())
                updater.create_update_package(release, version='1.0.1', component=channel,
                                              output=package, manifest_output=root / f'{channel}-new.json',
                                              previous_manifest=manifest_path)
                updater.apply_update(install, package, expected_component=channel)
                self.assertEqual((install / updater_name).read_bytes(), b'installed bootstrap')
            self.assertEqual((data / 'history.sqlite').read_bytes(), b'user history')
            self.assertEqual((data / 'config.yaml').read_text(encoding='utf-8'), 'user config')
            for channel in ('cli', 'gui'):
                self.assertEqual((root / f'localappdata/Programs/TestBox {channel.upper()}/app.exe').read_bytes(), f'{channel}-v2'.encode())


if __name__ == '__main__':
    unittest.main()
