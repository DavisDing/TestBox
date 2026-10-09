"""Native Inno/EXE acceptance on a disposable GitHub Actions Windows runner.

Never run installers on a developer workstation. This entry point refuses non-
Windows/non-Actions environments and existing TestBox registrations. It exercises
real compiled installers/updaters, but its delta is explicitly synthetic: it does
not claim compatibility with a different published binary release. No release
files, tags, or user data are removed. Logs and a JSON summary survive failures.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from testbox.updater import MANIFEST_NAME, _validate_manifest, sha256_file
from scripts.windows_smoke_fixtures import build_delta_fixture, corrupt_payload

COMMANDS = {"data.mock", "sql.parse", "sql.select", "evidence.build", "data.preview", "data.compare", "data.check", "sql.diff", "sql.preview"}
APP_IDS = {"cli": "A4D1265C-4D25-4935-ACFA-4053B154C2BB", "gui": "3B8BE87A-BBE1-4DE9-9FB7-3E5EC6D9A3C4"}


def require_disposable_runner(output: Path) -> Path:
    if (sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_OS") != "Windows"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"):
        raise RuntimeError("Native installer smoke requires a disposable GitHub-hosted Windows runner")
    runner = os.environ.get("RUNNER_TEMP")
    if not runner:
        raise RuntimeError("RUNNER_TEMP is required")
    temporary = Path(runner).resolve()
    output = output.resolve()
    if temporary not in output.parents or output.exists():
        raise RuntimeError("Smoke output must be a new child directory of RUNNER_TEMP")
    return output


def registry_install_dir(component: str) -> str | None:
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\TestBox\\{component.upper()}", 0,
                             winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except FileNotFoundError:
        return None
    with key as opened_key:
        try:
            value, kind = winreg.QueryValueEx(opened_key, "InstallDir")
        except FileNotFoundError as error:
            raise RuntimeError(f"Existing {component} registration is missing InstallDir") from error
        if kind != winreg.REG_SZ or not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"Invalid existing {component} InstallDir registration")
        return value


def registry_uninstall_exists(component: str) -> bool:
    import winreg
    key_name = f"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{{{APP_IDS[component]}}}_is1"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except FileNotFoundError:
        return False


def assert_no_existing_installations() -> None:
    for component in APP_IDS:
        if registry_install_dir(component) is not None or registry_uninstall_exists(component):
            raise RuntimeError(f"Refusing to touch an existing {component} installation")


def read_log(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def snapshot_files(paths: list[Path]) -> dict[str, str]:
    snapshot = {}
    for path in paths:
        if not path.is_file():
            raise AssertionError(f"Missing preserved file: {path}")
        snapshot[str(path)] = sha256_file(path)
    return snapshot


def assert_snapshot(expected: dict[str, str]) -> None:
    for name, digest in expected.items():
        path = Path(name)
        if not path.is_file() or sha256_file(path) != digest:
            raise AssertionError(f"Preserved file changed or disappeared: {path}")


def installed_manifest(root: Path) -> dict:
    return _validate_manifest(json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8")))


def verify_managed(root: Path, component: str, version: str) -> dict[str, str]:
    manifest = installed_manifest(root)
    if (manifest["component"], manifest["version"]) != (component, version):
        raise AssertionError(f"Installed manifest identity mismatch: {root}")
    paths = [root / MANIFEST_NAME]
    for item in manifest["files"]:
        path = root / Path(item["path"])
        if not path.is_file() or path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
            raise AssertionError(f"Installed managed file mismatch: {path}")
        paths.append(path)
    paths.append(root / f"TestBox-{component.upper()}-Updater.exe")
    return snapshot_files(paths)


@contextmanager
def hold_without_delete_share(path: Path):
    """Allow the updater to back up a file, but force its replacement to fail."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


class NativeSmoke:
    def __init__(self, *, output: Path, dist: Path, iscc: Path, version: str):
        self.output, self.dist, self.iscc, self.version = output, dist, iscc, version
        self.logs = output / "logs"
        self.logs.mkdir(parents=True)
        self.installs = {c: output / "installations" / f"{c.upper()} 安装" for c in APP_IDS}
        self.summary = {"status": "running", "delta_kind": "synthetic, not adjacent-release compatibility",
                        "version": version, "checks": [], "processes": []}
        self.protected: dict[str, str] = {}
        self.task_id: str | None = None
        self.task_result: dict | None = None

    def record(self, name: str) -> None:
        self.summary["checks"].append({"name": name, "status": "passed"})

    def run(self, executable: Path, arguments: list[str], name: str, *, expected=(0,), timeout=300) -> Path:
        output = self.logs / f"{name}-process.log"
        with output.open("wb") as stream:
            process = subprocess.Popen([str(executable), *arguments], cwd=self.output, stdout=stream,
                                       stderr=subprocess.STDOUT, env=os.environ.copy())
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Kill only the process tree owned by this invocation, never by image name.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=stream,
                               stderr=subprocess.STDOUT, timeout=30, check=False)
                process.wait(timeout=30)
                raise TimeoutError(f"Native smoke process timed out: {name}")
        self.summary["processes"].append({"name": name, "exit_code": exit_code, "log": str(output)})
        if exit_code not in expected:
            raise RuntimeError(f"Unexpected exit {exit_code} for {name}; expected {expected}\n{read_log(output)[-4000:]}")
        return output

    def setup(self, executable: Path, name: str, *, directory: Path | None = None, expected=(0,), uninstall=False) -> Path:
        log = self.logs / f"{name}-inno.log"
        arguments = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}"]
        if not uninstall:
            arguments += ["/SP-", "/NOCANCEL", "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/RESTARTEXITCODE=3010"]
        if directory is not None:
            arguments.append(f"/DIR={directory}")
        self.run(executable, arguments, name, expected=expected)
        if not log.is_file():
            raise AssertionError(f"Installer did not preserve its log: {name}")
        return log

    def compile_delta(self, component: str, package: Path, name: str) -> Path:
        source = ROOT / "installer" / ("TestBoxCLIUpdate.iss" if component == "cli" else "TestBoxUpdate.iss")
        compiled = self.output / "compiled"
        compiled.mkdir(exist_ok=True)
        self.run(self.iscc, [f"/DAppVersion={self.version}", f"/DUpdatePackage={package.name}",
                             f"/O{compiled}", f"/F{name}", str(source)], f"compile-{name}")
        executable = compiled / f"{name}.exe"
        if not executable.is_file():
            raise AssertionError(f"Compiler output missing: {executable}")
        return executable

    def cli(self, arguments: list[str], name: str) -> dict:
        executable = self.installs["cli"] / "TestBox" / "TestBox.exe"
        result = self.run(executable, ["--json", *arguments], name, timeout=90)
        return json.loads(read_log(result))

    def gui_host(self, name: str) -> None:
        bundle = self.installs["gui"] / "TestBox-GUI"
        workspace = self.output / "gui-host" / name
        for folder in ("input", "output", "logs"):
            (workspace / folder).mkdir(parents=True, exist_ok=True)
        request, response = workspace / "request.json", workspace / "response.json"
        request.write_text(json.dumps({"protocol_version": 1, "task_id": name, "command": "data.mock",
            "entry": "src.main:Plugin", "plugin_path": str(bundle / "_internal/plugins/data-generator"),
            "workspace": str(workspace), "params": {"count": 1, "format": "csv", "seed": 17,
                "fields": [{"name": "value", "generator": "constant", "options": {"value": "TEST DATA ONLY"}}]},
            "config": {}}, ensure_ascii=False), encoding="utf-8")
        self.run(bundle / "TestBox-GUI.exe", ["--plugin-host", "--request-file", str(request),
                                              "--response-file", str(response)], name, timeout=90)
        event = json.loads(response.read_text(encoding="utf-8"))
        if event.get("event") != "result" or event.get("result", {}).get("status") != "success":
            raise AssertionError(f"Installed GUI Host failed: {event}")
        files = event["result"].get("files", [])
        if not files or not all((workspace / "output" / file).is_file() for file in files):
            raise AssertionError("Installed GUI Host did not generate its declared output")

    def assert_registration(self, component: str) -> None:
        actual = registry_install_dir(component)
        if actual is None or Path(actual).resolve() != self.installs[component].resolve():
            raise AssertionError(f"Unexpected {component} registry installation path: {actual}")

    def failure(self, executable: Path, name: str, component: str, baseline: dict[str, str], *, token: str):
        log = self.setup(executable, name, expected=(7,))
        if token not in read_log(log):
            raise AssertionError(f"Failure log did not surface the expected updater diagnostic: {name}")
        assert_snapshot(baseline)
        assert_snapshot(self.protected)
        self.assert_registration(component)
        self.record(name)

    def uninstall(self, component: str, name: str) -> None:
        self.assert_registration(component)
        self.setup(self.installs[component] / "unins000.exe", name, uninstall=True)
        if registry_install_dir(component) is not None or registry_uninstall_exists(component):
            raise AssertionError(f"Uninstall left an active {component} registration")
        main = "TestBox/TestBox.exe" if component == "cli" else "TestBox-GUI/TestBox-GUI.exe"
        if (self.installs[component] / main).exists() or (self.installs[component] / MANIFEST_NAME).exists():
            raise AssertionError(f"Uninstall left registered {component} program files")
        assert_snapshot(self.protected)

    def execute(self) -> None:
        assert_no_existing_installations()
        data = Path(os.environ["LOCALAPPDATA"]) / "TestBox"
        fixture = data / f"installer-smoke-{uuid.uuid4().hex}"
        for relative in ("config.yaml", "plugins/local/sentinel.txt", "workspace/kept-task/result.json"):
            path = fixture / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("TEST DATA ONLY - preserve during update and uninstall\n", encoding="utf-8")
        self.protected = snapshot_files([p for p in fixture.rglob("*") if p.is_file()])
        installers = {c: self.dist / f"TestBox-{c.upper()}-Install-v{self.version}.exe" for c in APP_IDS}
        for component in APP_IDS:
            if not installers[component].is_file():
                raise FileNotFoundError(installers[component])
        for label, forbidden in (("user-data", data), ("other-default", Path(os.environ["LOCALAPPDATA"]) / "Programs/TestBox GUI")):
            log = self.setup(installers["cli"], f"reject-{label}", directory=forbidden, expected=(7,))
            if "重叠" not in read_log(log):
                raise AssertionError(f"Missing protected-path diagnostic: {label}")
            assert_no_existing_installations()
            assert_snapshot(self.protected)
            self.record(f"reject-{label}")

        baselines = {}
        for component in APP_IDS:
            other = "gui" if component == "cli" else "cli"
            self.setup(installers[component], f"install-{component}", directory=self.installs[component])
            self.assert_registration(component)
            baselines[component] = verify_managed(self.installs[component], component, self.version)
            if other in baselines:
                assert_snapshot(baselines[other])
            assert_snapshot(self.protected)
            for relative in ("keep-user.txt", ("TestBox" if component == "cli" else "TestBox-GUI") + "/keep-user.txt"):
                path = self.installs[component] / relative
                path.write_text("TEST DATA ONLY - unregistered installed file\n", encoding="utf-8")
                self.protected.update(snapshot_files([path]))
            self.record(f"install-{component}")

        overlap_log = self.setup(installers["cli"], "reject-registered-other", directory=self.installs["gui"], expected=(7,))
        if "重叠" not in read_log(overlap_log):
            raise AssertionError("Missing registered cross-component path diagnostic")
        for component in APP_IDS:
            assert_snapshot(baselines[component])
            self.assert_registration(component)
        assert_snapshot(self.protected)
        self.record("reject-registered-other")

        listing = self.cli(["plugin", "list"], "installed-cli-list")
        if {item["command"] for item in listing["commands"]} != COMMANDS or listing["unavailable"]:
            raise AssertionError(f"Installed CLI registry is incomplete: {listing}")
        task = self.cli(["run", "data.mock", "--set", "count=2", "--set", "format=csv", "--set", "seed=17",
                         "--set", 'fields=[{"name":"value","generator":"constant","options":{"value":"TEST DATA ONLY"}}]'], "installed-cli-task")
        if task["status"] != "success":
            raise AssertionError(task)
        task_root = Path(task["workspace"])
        expected_workspace = data / "workspace"
        if task_root.parent.resolve() != expected_workspace.resolve():
            raise AssertionError("Installed CLI did not use the shared user-data workspace")
        self.protected.update(snapshot_files([p for p in task_root.rglob("*") if p.is_file()]))
        self.task_id = task["task_id"]
        self.task_result = self.cli(["task", "result", self.task_id], "installed-cli-task-result")
        self.gui_host("installed-gui-host")
        self.record("installed-cli-and-gui-host")

        packages, updated = {}, {}
        marker = "000-installer-smoke-added.txt"
        for component in APP_IDS:
            old = installed_manifest(self.installs[component])
            changed = next(i["path"] for i in old["files"] if i["path"].endswith("/data-generator/README.md"))
            deleted = next(i["path"] for i in old["files"] if i["path"].endswith("/sql-parser/README.md"))
            content = (self.installs[component] / changed).read_bytes() + b"\nTEST DATA ONLY - synthetic delta\n"
            package = self.dist / f"installer-smoke-{component}-{uuid.uuid4().hex}.zip"
            updated[component] = build_delta_fixture(old, package, version=f"{self.version}+installer-smoke",
                replacements={marker: b"TEST DATA ONLY - added managed file\n", changed: content}, deleted=[deleted])
            packages[component] = (package, changed, deleted)

        good_cli, changed_cli, _ = packages["cli"]
        wrong_channel = self.compile_delta("cli", packages["gui"][0], "cli-wrong-component")
        self.failure(wrong_channel, "reject-wrong-component", "cli", baselines["cli"], token="组件不匹配")
        corrupt = self.dist / f"installer-smoke-corrupt-{uuid.uuid4().hex}.zip"
        corrupt_payload(good_cli, corrupt)
        self.failure(self.compile_delta("cli", corrupt, "cli-corrupt"), "reject-corrupt-hash", "cli",
                     baselines["cli"], token="校验失败")
        wrong_old = installed_manifest(self.installs["cli"])
        wrong_old["version"] = "0.0.0-not-the-installed-base"
        wrong_base = self.dist / f"installer-smoke-wrong-base-{uuid.uuid4().hex}.zip"
        build_delta_fixture(wrong_old, wrong_base, version=f"{self.version}+wrong-base-smoke",
                            replacements={marker: b"TEST DATA ONLY"}, deleted=[])
        self.failure(self.compile_delta("cli", wrong_base, "cli-wrong-base"), "reject-wrong-base", "cli",
                     baselines["cli"], token="更新包基于")
        good_cli_exe = self.compile_delta("cli", good_cli, "cli-good-delta")
        with hold_without_delete_share(self.installs["cli"] / changed_cli):
            self.failure(good_cli_exe, "locked-file-rollback", "cli", baselines["cli"], token="退出码")
        if (self.installs["cli"] / marker).exists():
            raise AssertionError("Rollback did not remove its earlier committed new file")
        if list(self.installs["cli"].parent.glob("testbox-update-backup-*")):
            raise AssertionError("Successful rollback leaked recovery backups")

        for component in APP_IDS:
            other = "gui" if component == "cli" else "cli"
            executable = good_cli_exe if component == "cli" else self.compile_delta("gui", packages[component][0], "gui-good-delta")
            self.setup(executable, f"update-{component}")
            baselines[component] = verify_managed(self.installs[component], component, updated[component]["version"])
            if (self.installs[component] / packages[component][2]).exists():
                raise AssertionError(f"Delta failed to delete its registered {component} file")
            assert_snapshot(baselines[other])
            assert_snapshot(self.protected)
            self.assert_registration(component)
            self.record(f"update-{component}")
        self.failure(good_cli_exe, "reject-replayed-delta", "cli", baselines["cli"], token="更新包基于")
        if self.cli(["task", "result", self.task_id], "history-after-updates") != self.task_result:
            raise AssertionError("Existing task history/result changed during native updates")
        self.gui_host("updated-gui-host")
        self.record("history-and-installed-host-after-updates")

        # Reinstall restores current release resources; unmanaged delta remnants
        # and user files are intentionally retained by the safe uninstall log.
        for component in APP_IDS:
            self.setup(installers[component], f"reinstall-{component}", directory=self.installs[component])
            verify_managed(self.installs[component], component, self.version)
            assert_snapshot(self.protected)
            self.record(f"reinstall-{component}")
        gui_before_uninstall = verify_managed(self.installs["gui"], "gui", self.version)
        self.uninstall("cli", "uninstall-cli")
        assert_snapshot(gui_before_uninstall)
        self.assert_registration("gui")
        self.record("uninstall-cli-preserves-gui-and-data")
        self.uninstall("gui", "uninstall-gui")
        self.record("uninstall-gui-preserves-data-and-unregistered-files")

    def cleanup_owned_installs(self) -> list[str]:
        failures = []
        for component, root in self.installs.items():
            try:
                actual = registry_install_dir(component)
                if actual is None:
                    continue
                if Path(actual).resolve() != root.resolve():
                    raise RuntimeError(f"Refusing cleanup of unexpected registered path: {actual}")
                self.uninstall(component, f"cleanup-{component}")
            except Exception as error:
                failures.append(f"{component}: {error}")
        return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--iscc", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        output = require_disposable_runner(args.output_root)
        if not args.iscc.is_file():
            raise FileNotFoundError(args.iscc)
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version) is None:
            raise ValueError("Expected the declared numeric release version")
        # Preflight before creating directories or synthetic shared user data.
        assert_no_existing_installations()
    except Exception as error:
        print(json.dumps({"status": "refused", "message": str(error)}))
        return 2
    smoke = NativeSmoke(output=output, dist=ROOT / "dist", iscc=args.iscc.resolve(), version=args.version)
    try:
        smoke.execute()
        smoke.summary["status"] = "passed"
    except Exception as error:
        smoke.summary.update(status="failed", message=str(error))
    finally:
        cleanup = smoke.cleanup_owned_installs()
        if cleanup:
            smoke.summary.update(status="failed", cleanup_errors=cleanup)
        (output / "summary.json").write_text(json.dumps(smoke.summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(smoke.summary, ensure_ascii=False, indent=2))
    return 0 if smoke.summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
