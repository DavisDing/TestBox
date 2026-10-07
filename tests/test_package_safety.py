"""Package quotas, portable paths and recoverable installation failures."""
from __future__ import annotations

import io
import shutil
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from testbox.core import plugin_packages as packages
from testbox.core.manifest import Manifest


class PackageSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "plugin.zip"
        self.output = self.root / "unpacked"
        self.output.mkdir()

    def write_archive(self, members):
        with zipfile.ZipFile(self.archive, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in members:
                archive.writestr(name, content)

    def make_plugin(self, directory, version="1.0.0", schema="schema.json"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.yaml").write_text(
            f"schema_version: 1\nname: safety-plugin\nversion: {version}\n"
            "description: Safety fixture\ncategory: test\ncore_compatibility: '>=1.0.0'\n"
            "entry: main:Plugin\ncommands:\n  - name: safety.run\n"
            f"    input_schema: '{schema}'\n"
            "capabilities:\n  concurrency: true\n  network: false\n"
            "  filesystem: output-only\n  resources: []\n", encoding="utf-8",
        )
        (directory / "main.py").write_text("# Fixture only; never executed.\n", encoding="utf-8")
        (directory / "schema.json").write_text('{"type": "object"}', encoding="utf-8")
        return directory

    def test_entry_quota_checked_before_any_extraction(self):
        self.write_archive([("one", b"1"), ("two", b"2")])
        with patch.object(packages, "MAX_ARCHIVE_ENTRIES", 1):
            with self.assertRaisesRegex(packages.PluginPackageError, "条目数"):
                packages._unpack_archive(self.archive, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_file_quota_checked_before_any_extraction(self):
        self.write_archive([("small", b"1"), ("large", b"12345")])
        with patch.object(packages, "MAX_ARCHIVE_FILE_BYTES", 4):
            with self.assertRaisesRegex(packages.PluginPackageError, "单项大小"):
                packages._unpack_archive(self.archive, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_total_quota_checked_before_any_extraction(self):
        self.write_archive([("one", b"123"), ("two", b"456")])
        with patch.object(packages, "MAX_ARCHIVE_TOTAL_BYTES", 5):
            with self.assertRaisesRegex(packages.PluginPackageError, "总大小"):
                packages._unpack_archive(self.archive, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_quotas_accept_exact_boundary_and_both_separators(self):
        self.write_archive([("nested\\", b""), ("nested\\one", b"123"), ("two", b"456")])
        with patch.object(packages, "MAX_ARCHIVE_ENTRIES", 3), \
             patch.object(packages, "MAX_ARCHIVE_FILE_BYTES", 3), \
             patch.object(packages, "MAX_ARCHIVE_TOTAL_BYTES", 6), \
             patch.object(packages, "ARCHIVE_CHUNK_BYTES", 2), \
             patch.object(zipfile.ZipFile, "extractall", side_effect=AssertionError("not streaming")):
            packages._unpack_archive(self.archive, self.output)
        self.assertEqual((self.output / "nested/one").read_bytes(), b"123")
        self.assertEqual((self.output / "two").read_bytes(), b"456")

    def test_invalid_paths_rejected_before_writing_valid_member(self):
        names = [
            "../escape", "nested/../escape", "..\\escape", "nested\\..\\escape",
            "/absolute", "\\absolute", "C:/absolute", "C:\\absolute", "C:relative",
            "//server/share/file", "\\\\server\\share\\file", "file:stream",
            "./file", "nested//file", "NUL.txt", "COM¹.log", "file.", "file ", "bad?name", "bad\x01name",
        ]
        for name in names:
            with self.subTest(name=name):
                self.write_archive([("valid", b"ok"), (name, b"bad")])
                with self.assertRaisesRegex(packages.PluginPackageError, "非法路径"):
                    packages._unpack_archive(self.archive, self.output)
                self.assertEqual(list(self.output.iterdir()), [])
        self.assertFalse((self.root / "escape").exists())

    def test_nul_in_original_member_name_is_rejected(self):
        info = zipfile.ZipInfo("safe\x00../escape")
        with self.assertRaisesRegex(packages.PluginPackageError, "非法路径"):
            packages._archive_relative_path(info)

    def test_links_and_special_files_are_rejected(self):
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK):
            with self.subTest(kind=kind):
                info = zipfile.ZipInfo("link")
                info.create_system = 3
                info.external_attr = (kind | 0o777) << 16
                self.write_archive([(info, b"../escape")])
                with self.assertRaisesRegex(packages.PluginPackageError, "链接或特殊文件"):
                    packages._unpack_archive(self.archive, self.output)
                self.assertEqual(list(self.output.iterdir()), [])

    def test_windows_reparse_point_is_rejected(self):
        info = zipfile.ZipInfo("link")
        info.create_system = 0
        info.external_attr = 0x400
        self.write_archive([(info, b"target")])
        with self.assertRaisesRegex(packages.PluginPackageError, "链接或特殊文件"):
            packages._unpack_archive(self.archive, self.output)

    def test_duplicate_portable_names_rejected_before_extraction(self):
        for names in (("File", "file"), ("nested/file", "nested\\file")):
            with self.subTest(names=names):
                self.write_archive([(names[0], b"1"), (names[1], b"2")])
                with self.assertRaisesRegex(packages.PluginPackageError, "重复路径"):
                    packages._unpack_archive(self.archive, self.output)
                self.assertEqual(list(self.output.iterdir()), [])

    def test_directory_payload_is_rejected(self):
        self.write_archive([("folder/", b"unexpected")])
        with self.assertRaisesRegex(packages.PluginPackageError, "目录条目"):
            packages._unpack_archive(self.archive, self.output)

    def test_existing_destination_symlink_cannot_escape(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.output / "nested").symlink_to(outside, target_is_directory=True)
        self.write_archive([("nested/file", b"bad")])
        with self.assertRaisesRegex(packages.PluginPackageError, "非法路径"):
            packages._unpack_archive(self.archive, self.output)
        self.assertEqual(list(outside.iterdir()), [])

    def test_stream_quota_rejects_underreported_size_without_overwriting(self):
        self.write_archive([("file", b"123")])
        with patch.object(zipfile.ZipFile, "open", return_value=io.BytesIO(b"123456789")), \
             patch.object(packages, "MAX_ARCHIVE_FILE_BYTES", 5), \
             patch.object(packages, "MAX_ARCHIVE_TOTAL_BYTES", 5):
            with self.assertRaisesRegex(packages.PluginPackageError, "实际解压大小超过"):
                packages._unpack_archive(self.archive, self.output)
        self.assertLessEqual((self.output / "file").stat().st_size, 5)

    def test_stream_total_quota_counts_multiple_members(self):
        self.write_archive([("one", b"123"), ("two", b"456")])
        with patch.object(zipfile.ZipFile, "open", side_effect=[io.BytesIO(b"123"), io.BytesIO(b"456789")]), \
             patch.object(packages, "MAX_ARCHIVE_FILE_BYTES", 10), \
             patch.object(packages, "MAX_ARCHIVE_TOTAL_BYTES", 6):
            with self.assertRaisesRegex(packages.PluginPackageError, "实际解压大小超过"):
                packages._unpack_archive(self.archive, self.output)
        self.assertEqual((self.output / "one").read_bytes(), b"123")
        self.assertLessEqual((self.output / "two").stat().st_size, 3)

    def test_stream_declared_size_mismatch_is_rejected(self):
        for content in (b"12", b"1234"):
            with self.subTest(content=content):
                output = self.root / f"mismatch-{len(content)}"
                self.write_archive([("file", b"123")])
                with patch.object(zipfile.ZipFile, "open", return_value=io.BytesIO(content)):
                    with self.assertRaisesRegex(packages.PluginPackageError, "声明不符"):
                        packages._unpack_archive(self.archive, output)

    def test_bad_zip_has_package_error(self):
        self.archive.write_bytes(b"not a ZIP")
        with self.assertRaisesRegex(packages.PluginPackageError, "有效 ZIP"):
            packages._unpack_archive(self.archive, self.output)

    def test_preview_and_install_share_quota_validation(self):
        source = self.make_plugin(self.root / "source")
        packages.package_plugin(source, self.archive)
        plugins = self.root / "plugins"
        with patch.object(packages, "MAX_ARCHIVE_ENTRIES", 1):
            for operation in (lambda: packages.inspect_plugin(self.archive), lambda: packages.install_plugin(self.archive, plugins)):
                with self.assertRaises(packages.PluginPackageError):
                    operation()
        self.assertFalse(plugins.exists())

    def installation_fixture(self):
        plugins = self.root / "plugins"
        target = self.make_plugin(plugins / "safety-plugin")
        (target / "user-data.txt").write_text("keep old data", encoding="utf-8")
        source = self.make_plugin(self.root / "source", "2.0.0")
        return plugins, target, source

    def assert_old_installation(self, target):
        self.assertEqual(Manifest.load(target / "manifest.yaml").version, "1.0.0")
        self.assertEqual((target / "user-data.txt").read_text(encoding="utf-8"), "keep old data")

    def test_successful_overwrite_cleans_backup_only_after_activation(self):
        plugins, target, source = self.installation_fixture()
        original_rmtree = shutil.rmtree
        cleaned_backups = []

        def checked_rmtree(path, *args, **kwargs):
            if Path(path).name.startswith(".safety-plugin.previous-"):
                self.assertEqual(Manifest.load(target / "manifest.yaml").version, "2.0.0")
                cleaned_backups.append(Path(path))
            return original_rmtree(path, *args, **kwargs)

        with patch.object(packages.shutil, "rmtree", side_effect=checked_rmtree):
            manifest = packages.install_plugin(source, plugins, force=True)
        self.assertEqual(manifest.version, "2.0.0")
        self.assertEqual(len(cleaned_backups), 1)
        self.assertEqual(list(plugins.iterdir()), [target])

    def test_activation_failure_restores_old_directory(self):
        plugins, target, source = self.installation_fixture()
        original_replace = Path.replace

        def replace(path, destination):
            if path.name.startswith(".safety-plugin.installing-"):
                raise OSError("injected activation failure")
            return original_replace(path, destination)

        with patch.object(Path, "replace", new=replace):
            with self.assertRaisesRegex(packages.PluginPackageError, "激活失败"):
                packages.install_plugin(source, plugins, force=True)
        self.assert_old_installation(target)
        self.assertEqual(list(plugins.iterdir()), [target])

    def test_post_activation_manifest_failure_also_rolls_back(self):
        plugins, target, source = self.installation_fixture()
        original_load = Manifest.load

        def load(path):
            if path == target / "manifest.yaml":
                raise ValueError("injected final validation failure")
            return original_load(path)

        with patch.object(Manifest, "load", side_effect=load):
            with self.assertRaisesRegex(packages.PluginPackageError, "final validation"):
                packages.install_plugin(source, plugins, force=True)
        self.assert_old_installation(target)

    def test_rollback_failure_preserves_both_versions_and_reports_paths(self):
        plugins, target, source = self.installation_fixture()
        original_replace = Path.replace

        def replace(path, destination):
            if path.name.startswith(".safety-plugin.installing-"):
                raise OSError("injected activation failure")
            if path.name.startswith(".safety-plugin.previous-"):
                raise OSError("injected rollback failure")
            return original_replace(path, destination)

        with patch.object(Path, "replace", new=replace):
            with self.assertRaisesRegex(packages.PluginPackageError, "回滚失败") as caught:
                packages.install_plugin(source, plugins, force=True)
        backup, = plugins.glob(".safety-plugin.previous-*")
        replacement, = plugins.glob(".safety-plugin.installing-*")
        self.assert_old_installation(backup)
        self.assertEqual(Manifest.load(replacement / "manifest.yaml").version, "2.0.0")
        self.assertIn(str(backup), str(caught.exception))
        self.assertIn(str(replacement), str(caught.exception))
        self.assertFalse(target.exists())

    def test_post_activation_rollback_failure_keeps_old_and_rejected_new(self):
        plugins, target, source = self.installation_fixture()
        original_load = Manifest.load
        original_replace = Path.replace

        def load(path):
            if path == target / "manifest.yaml":
                raise ValueError("injected final validation failure")
            return original_load(path)

        def replace(path, destination):
            if path.name.startswith(".safety-plugin.previous-"):
                raise OSError("injected restore failure")
            return original_replace(path, destination)

        with patch.object(Manifest, "load", side_effect=load), patch.object(Path, "replace", new=replace):
            with self.assertRaisesRegex(packages.PluginPackageError, "回滚失败"):
                packages.install_plugin(source, plugins, force=True)
        backup, = plugins.glob(".safety-plugin.previous-*")
        replacement, = plugins.glob(".safety-plugin.installing-*")
        self.assert_old_installation(backup)
        self.assertEqual(Manifest.load(replacement / "manifest.yaml").version, "2.0.0")

    def test_first_install_activation_failure_preserves_new_directory(self):
        source = self.make_plugin(self.root / "source")
        plugins = self.root / "plugins"
        original_replace = Path.replace

        def replace(path, destination):
            if path.name.startswith(".safety-plugin.installing-"):
                raise OSError("injected activation failure")
            return original_replace(path, destination)

        with patch.object(Path, "replace", new=replace):
            with self.assertRaisesRegex(packages.PluginPackageError, "激活失败"):
                packages.install_plugin(source, plugins)
        replacement, = plugins.glob(".safety-plugin.installing-*")
        self.assertEqual(Manifest.load(replacement / "manifest.yaml").version, "1.0.0")
        self.assertFalse((plugins / "safety-plugin").exists())

    def test_recovery_from_failed_rollback_does_not_delete_previous_recovery(self):
        plugins, target, source = self.installation_fixture()
        old_backup = plugins / ".safety-plugin.previous"
        old_replacement = plugins / ".safety-plugin.installing"
        for directory in (old_backup, old_replacement):
            directory.mkdir()
            (directory / "recover.txt").write_text("recovery data", encoding="utf-8")
        packages.install_plugin(source, plugins, force=True)
        for directory in (old_backup, old_replacement):
            self.assertEqual((directory / "recover.txt").read_text(encoding="utf-8"), "recovery data")

    def test_backup_rename_failure_keeps_old_installation(self):
        plugins, target, source = self.installation_fixture()
        original_replace = Path.replace

        def replace(path, destination):
            if path == target:
                raise OSError("injected backup failure")
            return original_replace(path, destination)

        with patch.object(Path, "replace", new=replace):
            with self.assertRaisesRegex(packages.PluginPackageError, "backup failure"):
                packages.install_plugin(source, plugins, force=True)
        self.assert_old_installation(target)
        self.assertEqual(list(plugins.iterdir()), [target])

    def test_copy_failure_keeps_old_installation(self):
        plugins, target, source = self.installation_fixture()
        original_copytree = shutil.copytree

        def copytree(src, dst, *args, **kwargs):
            result = original_copytree(src, dst, *args, **kwargs)
            if Path(dst).name.startswith(".safety-plugin.installing-"):
                raise OSError("injected copy failure")
            return result

        with patch.object(packages.shutil, "copytree", side_effect=copytree):
            with self.assertRaisesRegex(OSError, "copy failure"):
                packages.install_plugin(source, plugins, force=True)
        self.assert_old_installation(target)
        self.assertEqual(list(plugins.iterdir()), [target])

    def test_cleanup_failure_keeps_new_active_and_old_recoverable(self):
        plugins, target, source = self.installation_fixture()
        original_rmtree = shutil.rmtree

        def rmtree(path, *args, **kwargs):
            if Path(path).name.startswith(".safety-plugin.previous-"):
                raise OSError("injected cleanup failure")
            return original_rmtree(path, *args, **kwargs)

        with patch.object(packages.shutil, "rmtree", side_effect=rmtree):
            with self.assertRaisesRegex(packages.PluginPackageError, "已激活.*清理失败"):
                packages.install_plugin(source, plugins, force=True)
        self.assertEqual(Manifest.load(target / "manifest.yaml").version, "2.0.0")
        backup, = plugins.glob(".safety-plugin.previous-*")
        self.assert_old_installation(backup)

    def test_first_install_and_non_force_overwrite_contract(self):
        source = self.make_plugin(self.root / "source")
        plugins = self.root / "plugins"
        self.assertEqual(packages.install_plugin(source, plugins).version, "1.0.0")
        with self.assertRaisesRegex(packages.PluginPackageError, "已安装"):
            packages.install_plugin(source, plugins)

    def test_manifest_schema_resolve_rejects_traversal_and_external_absolute(self):
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        for index, schema in enumerate(("../outside.json", "nested/../../outside.json", str(outside))):
            with self.subTest(schema=schema):
                source = self.make_plugin(self.root / f"plugin-{index}", schema=schema)
                (source / "nested").mkdir()
                with self.assertRaisesRegex(ValueError, "逃逸插件目录"):
                    Manifest.load(source / "manifest.yaml")

    def test_manifest_schema_resolve_rejects_symlink_escape(self):
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        source = self.make_plugin(self.root / "source", schema="linked.json")
        (source / "linked.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "逃逸插件目录"):
            Manifest.load(source / "manifest.yaml")

    def test_manifest_schema_accepts_normalized_in_directory_path(self):
        source = self.make_plugin(self.root / "source", schema="nested/../schema.json")
        (source / "nested").mkdir()
        manifest = Manifest.load(source / "manifest.yaml")
        self.assertEqual(manifest.commands[0].input_schema, "nested/../schema.json")


if __name__ == "__main__":
    unittest.main()
