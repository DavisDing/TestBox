"""The Office ZIP uses the same package/install/Host path as other plugins."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from testbox.core.runtime import Runtime


ROOT = Path(__file__).resolve().parents[1]


class OfficeDistributionTests(unittest.TestCase):
    def test_package_preview_install_inspect_without_system_dependency(self):
        with tempfile.TemporaryDirectory(prefix="testbox-office-package-") as directory:
            root = Path(directory)
            runtime = Runtime(root)
            self.addCleanup(runtime.close)
            archive = runtime.package_plugin(
                ROOT / "plugins" / "office-convert", root / "office-convert.zip"
            )
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                for name in ("manifest.yaml", "src/main.py", "schemas/office.convert.json",
                             "schemas/office.inspect.json", "README.md"):
                    self.assertIn(name, names)
                self.assertFalse(any("__pycache__" in name or name.endswith(".pyc")
                                     for name in names))
            preview = runtime.preview_plugin_install(archive)
            self.assertEqual(preview["name"], "office-convert")
            self.assertEqual(set(preview["commands"]), {"office.convert", "office.inspect"})
            self.assertEqual(runtime.list_commands(), {})
            self.assertFalse((root / "plugins" / "office-convert").exists())
            manifest = runtime.install_plugin(archive)
            self.assertEqual(manifest.name, "office-convert")
            self.assertEqual(set(runtime.list_commands()), {"office.convert", "office.inspect"})
            # Inspect must stay useful on a user's installation without LO.
            with patch.dict("os.environ", {
                "TESTBOX_OFFICE_CONVERT_SOFFICE_PATH": str(root / "missing-engine")
            }):
                task, result = runtime.run("office.inspect", {})
            self.assertEqual(result.status, "success", result.to_dict())
            self.assertIs(result.data["available"], False)
            self.assertEqual(runtime.get_task_result(task), result.to_dict())
            self.assertIn(task, runtime.get_task_report(task))
            runtime.uninstall_plugin("office-convert")
            self.assertEqual(runtime.list_commands(), {})
            self.assertIsNotNone(runtime.get_task_result(task))


if __name__ == "__main__":
    unittest.main()
