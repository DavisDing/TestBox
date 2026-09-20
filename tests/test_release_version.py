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
        runtime_version = re.search(r'^__version__ = "([^"]+)"$', (root / "testbox" / "__init__.py").read_text(encoding="utf-8"), re.MULTILINE)
        installer_version = re.search(r'^#define AppVersion "([^"]+)"$', (root / "installer" / "TestBox.iss").read_text(encoding="utf-8"), re.MULTILINE)
        update_script = (root / "installer" / "TestBoxUpdate.iss").read_text(encoding="utf-8")
        update_version = re.search(r'^#define AppVersion "([^"]+)"$', update_script, re.MULTILINE)
        update_package = re.search(r'^#define UpdatePackage "TestBox-update-v([^"]+)\.zip"$', update_script, re.MULTILINE)

        self.assertIsNotNone(runtime_version)
        self.assertIsNotNone(installer_version)
        self.assertIsNotNone(update_version)
        self.assertIsNotNone(update_package)
        self.assertEqual({project_version, runtime_version.group(1), installer_version.group(1), update_version.group(1), update_package.group(1)}, {project_version})

    def test_sync_updates_all_release_version_declarations(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            (root / "installer").mkdir()
            (root / "testbox").mkdir()
            (root / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
            (root / "testbox" / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
            (root / "installer" / "TestBox.iss").write_text('#define AppVersion "1.0.0"\n', encoding="utf-8")
            (root / "installer" / "TestBoxUpdate.iss").write_text('#define AppVersion "1.0.0"\n#define UpdatePackage "TestBox-update-v1.0.0.zip"\n', encoding="utf-8")

            changed = sync_version_files("1.0.1", root)

            self.assertEqual(len(changed), 5)
            self.assertIn('version = "1.0.1"', (root / "pyproject.toml").read_text(encoding="utf-8"))
            self.assertIn('__version__ = "1.0.1"', (root / "testbox" / "__init__.py").read_text(encoding="utf-8"))
            self.assertIn('#define AppVersion "1.0.1"', (root / "installer" / "TestBox.iss").read_text(encoding="utf-8"))
            update_script = (root / "installer" / "TestBoxUpdate.iss").read_text(encoding="utf-8")
            self.assertIn('#define AppVersion "1.0.1"', update_script)
            self.assertIn('#define UpdatePackage "TestBox-update-v1.0.1.zip"', update_script)


if __name__ == "__main__":
    unittest.main()
