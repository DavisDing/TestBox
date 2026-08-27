from __future__ import annotations

import tempfile
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
