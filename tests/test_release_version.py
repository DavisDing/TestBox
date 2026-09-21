from __future__ import annotations

import re
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.release_version import next_release_version, sync_version_files


class ReleaseVersionTests(unittest.TestCase):
    def test_first_release_uses_declared_version(self):
        self.assertEqual(next_release_version("1.2.3", None), "1.2.3")

    def test_automatic_release_increments_patch(self):
        self.assertEqual(next_release_version("1.0.1", "v1.0.1"), "1.0.2")
        self.assertEqual(next_release_version("1.0.0", "v1.4.9"), "1.4.10")

    def test_declared_version_is_synchronized_to_latest_patch(self):
        self.assertEqual(next_release_version("2.0.0", "v1.4.9"), "1.4.10")

    def test_repository_release_version_declarations_are_consistent(self):
        root = Path(__file__).resolve().parents[1]
        project_version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        runtime_source = (root / "testbox" / "__init__.py").read_text(encoding="utf-8")
        compile(runtime_source, str(root / "testbox" / "__init__.py"), "exec")

        version_declarations = {
            "runtime": re.findall(r'^__version__ = "([^"]+)"$', runtime_source, re.MULTILINE),
            "gui_installer": self._version_declarations(root / "installer" / "TestBox.iss"),
            "cli_installer": self._version_declarations(root / "installer" / "TestBoxCLI.iss"),
            "gui_updater": self._version_declarations(root / "installer" / "TestBoxUpdate.iss"),
            "cli_updater": self._version_declarations(root / "installer" / "TestBoxCLIUpdate.iss"),
            "gui_update_package": self._update_package_declarations(
                root / "installer" / "TestBoxUpdate.iss", "TestBox-GUI"
            ),
            "cli_update_package": self._update_package_declarations(
                root / "installer" / "TestBoxCLIUpdate.iss", "TestBox-CLI"
            ),
        }
        for name, declarations in version_declarations.items():
            self.assertEqual(declarations, [project_version], f"{name} version declaration is missing or duplicated")

    def test_sync_updates_all_release_version_declarations(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            self._write_version_files(root)

            changed = sync_version_files("1.0.1", root)

            self.assertEqual(len(changed), 6, "eight declarations span six version files")
            self.assertIn('version = "1.0.1"', (root / "pyproject.toml").read_text(encoding="utf-8"))
            self.assertIn('__version__ = "1.0.1"', (root / "testbox" / "__init__.py").read_text(encoding="utf-8"))
            self.assertIn('#define AppVersion "1.0.1"', (root / "installer" / "TestBox.iss").read_text(encoding="utf-8"))
            self.assertIn('#define AppVersion "1.0.1"', (root / "installer" / "TestBoxCLI.iss").read_text(encoding="utf-8"))
            gui_update = (root / "installer" / "TestBoxUpdate.iss").read_text(encoding="utf-8")
            self.assertIn('#define AppVersion "1.0.1"', gui_update)
            self.assertIn('#define UpdatePackage "TestBox-GUI-update-v1.0.1.zip"', gui_update)
            cli_update = (root / "installer" / "TestBoxCLIUpdate.iss").read_text(encoding="utf-8")
            self.assertIn('#define AppVersion "1.0.1"', cli_update)
            self.assertIn('#define UpdatePackage "TestBox-CLI-update-v1.0.1.zip"', cli_update)

    def test_sync_rejects_duplicate_release_version_declarations(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            self._write_version_files(root)
            runtime_path = root / "testbox" / "__init__.py"
            runtime_path.write_text('__version__ = "1.0.0"\n__version__ = "1.0.0"\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "exactly one release version declaration"):
                sync_version_files("1.0.1", root)

            self.assertIn('version = "1.0.0"', (root / "pyproject.toml").read_text(encoding="utf-8"))
            self.assertIn('__version__ = "1.0.0"', runtime_path.read_text(encoding="utf-8"))

    @staticmethod
    def _version_declarations(path: Path) -> list[str]:
        return re.findall(r'^#define AppVersion "([^"]+)"$', path.read_text(encoding="utf-8"), re.MULTILINE)

    @staticmethod
    def _update_package_declarations(path: Path, prefix: str) -> list[str]:
        return re.findall(
            rf'^#define UpdatePackage "{re.escape(prefix)}-update-v([^"]+)\.zip"$',
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )

    @staticmethod
    def _write_version_files(root: Path) -> None:
        (root / "installer").mkdir()
        (root / "testbox").mkdir()
        (root / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
        (root / "testbox" / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
        (root / "installer" / "TestBox.iss").write_text('#define AppVersion "1.0.0"\n', encoding="utf-8")
        (root / "installer" / "TestBoxCLI.iss").write_text('#define AppVersion "1.0.0"\n', encoding="utf-8")
        (root / "installer" / "TestBoxUpdate.iss").write_text(
            '#define AppVersion "1.0.0"\n#define UpdatePackage "TestBox-GUI-update-v1.0.0.zip"\n',
            encoding="utf-8",
        )
        (root / "installer" / "TestBoxCLIUpdate.iss").write_text(
            '#define AppVersion "1.0.0"\n#define UpdatePackage "TestBox-CLI-update-v1.0.0.zip"\n',
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
