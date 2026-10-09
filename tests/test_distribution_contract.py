"""Distribution layout contracts; real installed-wheel smoke runs in CI."""
from __future__ import annotations

from pathlib import Path
import ast
import re
import shlex
import shutil
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from testbox.core.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


class DistributionContractTests(unittest.TestCase):
    def test_build_hook_and_assets_are_declared(self):
        configuration = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(configuration["tool"]["setuptools"]["cmdclass"]["build_py"], "testbox_build.BundledBuildPy")
        self.assertIn("assets/*.ico", configuration["tool"]["setuptools"]["package-data"]["testbox"])
        manifest = (ROOT / "MANIFEST.in").read_text()
        self.assertIn("include testbox_build.py", manifest)
        self.assertIn("recursive-include plugins", manifest)

    def test_official_archives_release_and_smoke_inventories_match(self):
        supported = {"data-generator", "sql-parser", "sql-select", "evidence-tool",
                     "data-preview", "data-compare", "schema-diff", "data-check"}
        commands = {"data.mock", "sql.parse", "sql.select", "evidence.build",
                    "data.preview", "data.compare", "data.check", "sql.diff", "sql.preview"}
        sources = {path.name: path for path in (ROOT / "plugins").iterdir()
                   if path.is_dir() and (path / "manifest.yaml").is_file()}
        self.assertEqual(set(sources), supported)
        self.assertFalse((ROOT / "plugins" / "office-convert").exists())
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime(Path(directory))
            archived_commands = set()
            try:
                for name, source in sources.items():
                    with self.subTest(plugin=name):
                        archive = runtime.package_plugin(source, Path(directory) / f"{name}.zip")
                        manifest = runtime.validate_plugin(archive)
                        self.assertEqual(manifest.name, name)
                        archived_commands.update(command.name for command in manifest.commands)
            finally:
                # Windows cannot remove an open task-history database.
                runtime.close()
        self.assertEqual(archived_commands, commands)
        for filename in ("smoke_installed.py", "smoke_windows_installers.py"):
            with self.subTest(smoke=filename):
                source = (ROOT / "scripts" / filename).read_text(encoding="utf-8")
                self.assertNotIn("office_inspect", source)
                tree = ast.parse(source)
                declared = next(ast.literal_eval(node.value) for node in tree.body
                                if isinstance(node, ast.Assign)
                                and any(isinstance(target, ast.Name) and target.id == "COMMANDS"
                                        for target in node.targets))
                self.assertEqual(declared, commands)
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        packaged = set(re.findall(r"plugin package plugins/([a-z0-9-]+)", workflow))
        for loop in re.findall(r"for plugin in ([^;\n]+); do", workflow):
            packaged.update(shlex.split(loop))
        self.assertEqual(packaged, supported)

    def test_installed_layout_uses_readonly_bundles_and_writable_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / "site-packages" / "testbox"
            (package / "core").mkdir(parents=True)
            fake_runtime_file = package / "core" / "runtime.py"
            fake_runtime_file.touch()
            bundled = package / "_bundled_plugins"
            shutil.copytree(ROOT / "plugins", bundled, ignore=shutil.ignore_patterns("__pycache__"))
            data = root / "data"
            with patch("testbox.core.runtime.__file__", str(fake_runtime_file)), patch.object(Runtime, "_user_data_dir", return_value=data):
                runtime = Runtime()
                try:
                    self.assertEqual(runtime.root, package)
                    self.assertEqual(runtime.bundled_plugins_dir, bundled)
                    self.assertEqual(runtime.plugins_dir, data / "plugins")
                    self.assertEqual(runtime.workspace_dir, data / "workspace")
                    self.assertEqual(set(runtime.list_commands()), {"data.mock", "sql.parse", "sql.select", "evidence.build", "data.preview", "data.compare", "data.check", "sql.diff", "sql.preview"})
                    self.assertFalse(runtime.can_uninstall_plugin("data-generator"))
                    self.assertFalse((package / "workspace").exists())
                finally:
                    runtime.close()

    def test_explicit_root_never_imports_packaged_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / "installed" / "testbox"
            (package / "core").mkdir(parents=True)
            (package / "_bundled_plugins").mkdir()
            explicit = root / "empty-root"
            with patch("testbox.core.runtime.__file__", str(package / "core" / "runtime.py")):
                runtime = Runtime(explicit)
                try:
                    self.assertEqual(runtime.list_commands(), {})
                    self.assertEqual(runtime.plugins_dir, explicit / "plugins")
                    self.assertEqual(runtime.workspace_dir, explicit / "workspace")
                finally:
                    runtime.close()

    def test_packaged_install_ignores_unrelated_cwd_plugins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / "site-packages" / "testbox"
            (package / "_bundled_plugins").mkdir(parents=True)
            (package / "core").mkdir()
            unrelated = root / "unrelated"
            (unrelated / "plugins").mkdir(parents=True)
            with patch("testbox.core.runtime.__file__", str(package / "core" / "runtime.py")), patch.object(Path, "cwd", return_value=unrelated):
                self.assertEqual(Runtime._application_root(), package)


if __name__ == "__main__":
    unittest.main()
