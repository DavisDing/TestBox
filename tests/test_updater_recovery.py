from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from testbox import updater


class UpdaterRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.install = self.root / 'install'
        self.release = self.root / 'release'
        self.release.mkdir()
        for name, content in {'a.txt': 'old-a', 'b.txt': 'old-b', 'remove.txt': 'old-remove'}.items():
            (self.release / name).write_text(content, encoding='utf-8')
        self.old_manifest = self.root / 'old.json'
        updater.create_update_package(self.release, version='1.0.0', component='cli',
                                      output=self.root / 'old.zip', manifest_output=self.old_manifest)
        updater.apply_update(self.install, self.root / 'old.zip')
        (self.install / 'user.txt').write_text('user data', encoding='utf-8')
        (self.release / 'a.txt').write_text('new-a', encoding='utf-8')
        (self.release / 'b.txt').write_text('new-b', encoding='utf-8')
        (self.release / 'remove.txt').unlink()
        (self.release / 'nested').mkdir()
        (self.release / 'nested/new.txt').write_text('new', encoding='utf-8')
        self.package = self.root / 'new.zip'
        self.manifest = updater.create_update_package(
            self.release, version='1.0.1', component='cli', output=self.package,
            manifest_output=self.root / 'new.json', previous_manifest=self.old_manifest)
        self.before = self.snapshot()

    def snapshot(self):
        return {p.relative_to(self.install).as_posix(): p.read_bytes()
                for p in self.install.rglob('*') if p.is_file()}

    def rewrite(self, manifest=None, extra=None):
        with zipfile.ZipFile(self.package) as archive:
            entries = [(i, archive.read(i)) for i in archive.infolist()]
        with zipfile.ZipFile(self.package, 'w') as archive:
            for info, content in entries:
                archive.writestr(info, json.dumps(manifest).encode() if manifest is not None
                                 and info.filename == updater.MANIFEST_NAME else content)
            if extra:
                for name, content in extra:
                    archive.writestr(name, content)

    def test_backup_failure_never_deletes_unbacked_up_files(self):
        original = updater.shutil.copy2
        def fail(source, destination, *args, **kwargs):
            if Path(source) == self.install / 'b.txt':
                raise OSError('backup unavailable')
            return original(source, destination, *args, **kwargs)
        with patch.object(updater.shutil, 'copy2', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'backup unavailable'):
                updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)

    def test_apply_failure_restores_files_manifest_and_new_directories(self):
        original = Path.replace
        def fail(path, target):
            if Path(target) == self.install / updater.MANIFEST_NAME:
                raise OSError('manifest commit failed')
            return original(path, target)
        with patch.object(Path, 'replace', autospec=True, side_effect=fail):
            with self.assertRaisesRegex(OSError, 'manifest commit failed'):
                updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)
        self.assertFalse((self.install / 'nested').exists())

    def test_recovery_failure_preserves_backup_and_attempts_other_restores(self):
        original_copy = updater.shutil.copy2
        original_replace = Path.replace
        backups = []
        original_mkdtemp = updater.tempfile.mkdtemp
        def mkdtemp(*args, **kwargs):
            if 'backup' in kwargs.get('prefix', ''):
                kwargs['dir'] = self.root
            value = original_mkdtemp(*args, **kwargs)
            if 'backup' in kwargs.get('prefix', ''):
                backups.append(Path(value))
            return value
        def fail_copy(source, destination, *args, **kwargs):
            if 'backup' in str(source) and Path(source).name == 'a.txt':
                raise OSError('restore denied')
            return original_copy(source, destination, *args, **kwargs)
        def fail_replace(path, target):
            if Path(target) == self.install / updater.MANIFEST_NAME:
                raise OSError('manifest commit failed')
            return original_replace(path, target)
        with patch.object(updater.tempfile, 'mkdtemp', side_effect=mkdtemp), \
             patch.object(updater.shutil, 'copy2', side_effect=fail_copy), \
             patch.object(Path, 'replace', autospec=True, side_effect=fail_replace):
            with self.assertRaisesRegex(Exception, '回滚.*备份') as raised:
                updater.apply_update(self.install, self.package)
        self.assertIn('manifest commit failed', str(raised.exception))
        self.assertIn('restore denied', str(raised.exception))
        self.assertTrue(backups[-1].is_dir())
        self.assertEqual((backups[-1] / 'a.txt').read_bytes(), self.before['a.txt'])
        self.assertEqual((self.install / 'b.txt').read_bytes(), self.before['b.txt'])
        self.assertEqual((self.install / 'remove.txt').read_bytes(), self.before['remove.txt'])
        self.assertEqual((self.install / updater.MANIFEST_NAME).read_bytes(), self.before[updater.MANIFEST_NAME])

    def test_delete_unmanaged_file_is_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['deleted_files'].append('user.txt')
        self.rewrite(manifest)
        with self.assertRaisesRegex(ValueError, '未登记'):
            updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)

    def test_changed_file_cannot_overwrite_unmanaged_file(self):
        (self.install / 'nested').mkdir()
        (self.install / 'nested/new.txt').write_text('user collision', encoding='utf-8')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '未登记'):
            updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), before)

    def test_reserved_files_cannot_be_deleted_or_changed(self):
        for path in (updater.MANIFEST_NAME, 'TestBox-CLI-Updater.exe', 'TestBox-GUI-Updater.exe',
                     'testbox-cli-updater.EXE', 'TestBox-Updater.exe'):
            with self.subTest(path=path):
                manifest = copy.deepcopy(self.manifest)
                manifest['deleted_files'] = [path]
                with self.assertRaisesRegex(ValueError, '受保护'):
                    updater._validate_manifest(manifest)
                manifest = copy.deepcopy(self.manifest)
                manifest['files'].append({'path': path, 'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()})
                with self.assertRaisesRegex(ValueError, '受保护'):
                    updater._validate_manifest(manifest)

    def test_duplicate_zip_member_is_rejected(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            self.rewrite(extra=[('a.txt', 'new-a')])
        with self.assertRaisesRegex(ValueError, '重复'):
            updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)

    def test_unlisted_archive_member_is_rejected(self):
        self.rewrite(extra=[('unexpected.txt', 'x')])
        with self.assertRaisesRegex(ValueError, '未登记'):
            updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)

    def test_windows_unsafe_paths_are_rejected_on_every_platform(self):
        for path in ('C:/outside', 'C:relative', 'file:stream', '/root', '../bad', 'x\\y',
                     '.', './a', 'a//b', 'a/', 'CON', 'aux.txt', 'dir/NUL.exe',
                     'a. ', 'dir./x', 'a\x00b', 'a?/b'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, '非法'):
                updater._safe_relative_path(path)

    def test_case_insensitive_manifest_collisions_are_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['files'].append(dict(manifest['files'][0], path='A.TXT'))
        with self.assertRaisesRegex(ValueError, '重复'):
            updater._validate_manifest(manifest)

    def test_downloaded_manifest_is_bound_to_archive(self):
        advertised = copy.deepcopy(self.manifest)
        advertised['version'] = '9.0.0'
        def download(url, destination):
            if url == 'https://example.test/manifest.json':
                destination.write_text(json.dumps(advertised), encoding='utf-8')
            else:
                destination.write_bytes(self.package.read_bytes())
        advertised['package_url'] = 'https://example.test/new.zip'
        with patch.object(updater, 'download', side_effect=download):
            with self.assertRaisesRegex(ValueError, '清单.*不一致'):
                updater.download_and_apply(self.install, manifest_url='https://example.test/manifest.json')
        self.assertEqual(self.snapshot(), self.before)

    def test_download_package_name_cannot_escape_temporary_directory(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['package'] = '../escape.zip'
        manifest['package_url'] = 'https://example.test/new.zip'
        def download(url, destination):
            destination.write_text(json.dumps(manifest), encoding='utf-8')
        with patch.object(updater, 'download', side_effect=download) as mocked:
            with self.assertRaisesRegex(ValueError, '非法'):
                updater.download_and_apply(self.install, manifest_url='https://example.test/manifest.json')
        self.assertEqual(mocked.call_count, 1)

    def test_expected_component_rejects_wrong_package_without_local_manifest(self):
        with self.assertRaisesRegex(ValueError, '组件不匹配'):
            updater.apply_update(self.root / 'empty', self.package, expected_component='gui')

    def test_failure_during_first_staged_copy_cleans_temporary_file(self):
        original = updater.shutil.copy2
        def fail(source, destination, *args, **kwargs):
            if 'testbox-update-' in str(source) and 'backup' not in str(source):
                Path(destination).write_bytes(b'partial')
                raise OSError('copy interrupted')
            return original(source, destination, *args, **kwargs)
        with patch.object(updater.shutil, 'copy2', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'copy interrupted'):
                updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)

    def test_failure_midway_through_deletions_restores_committed_replacements(self):
        original = Path.unlink
        def fail(path, *args, **kwargs):
            if path == self.install / 'remove.txt':
                raise PermissionError('file in use')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', autospec=True, side_effect=fail):
            with self.assertRaisesRegex(PermissionError, 'file in use'):
                updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)
        self.assertFalse((self.install / 'nested').exists())

    def test_retry_after_successful_rollback_can_complete(self):
        original = Path.replace
        def fail(path, target):
            if Path(target) == self.install / 'b.txt':
                raise PermissionError('file locked')
            return original(path, target)
        with patch.object(Path, 'replace', autospec=True, side_effect=fail):
            with self.assertRaisesRegex(PermissionError, 'file locked'):
                updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)
        result = updater.apply_update(self.install, self.package)
        self.assertEqual(result['version'], '1.0.1')
        self.assertEqual((self.install / 'user.txt').read_bytes(), self.before['user.txt'])

    def test_unchanged_file_corruption_is_rejected_before_mutation(self):
        # Treat b.txt as unchanged, but advertise a different hash.
        manifest = copy.deepcopy(self.manifest)
        manifest['changed_files'].remove('b.txt')
        with zipfile.ZipFile(self.package) as archive:
            entries = [(i.filename, archive.read(i)) for i in archive.infolist() if i.filename != 'b.txt']
        with zipfile.ZipFile(self.package, 'w') as archive:
            for name, content in entries:
                archive.writestr(name, json.dumps(manifest) if name == updater.MANIFEST_NAME else content)
        with self.assertRaisesRegex(ValueError, '未变化文件校验失败'):
            updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)

    def test_missing_delta_deletion_is_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['deleted_files'] = []
        self.rewrite(manifest)
        with self.assertRaisesRegex(ValueError, '删除文件.*不一致'):
            updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)

    def test_hash_failure_does_not_modify_installation(self):
        with zipfile.ZipFile(self.package) as archive:
            entries = [(i.filename, archive.read(i)) for i in archive.infolist()]
        with zipfile.ZipFile(self.package, 'w') as archive:
            for name, content in entries:
                archive.writestr(name, b'wrong' if name == 'a.txt' else content)
        with self.assertRaisesRegex(ValueError, '校验失败'):
            updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)

    def test_schema_one_and_future_versions_are_rejected(self):
        for version in (1, 3, True):
            manifest = dict(self.manifest, schema_version=version)
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, '不支持'):
                updater._validate_manifest(manifest)

    def test_manifest_validation_does_not_mutate_input(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['files'][0]['sha256'] = manifest['files'][0]['sha256'].upper()
        before = copy.deepcopy(manifest)
        updater._validate_manifest(manifest)
        self.assertEqual(manifest, before)

    def test_local_manifest_link_is_rejected_without_touching_target(self):
        outside = self.root / 'outside.json'
        outside.write_bytes(self.before[updater.MANIFEST_NAME])
        (self.install / updater.MANIFEST_NAME).unlink()
        (self.install / updater.MANIFEST_NAME).symlink_to(outside)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            updater.apply_update(self.install, self.package)
        self.assertEqual(outside.read_bytes(), self.before[updater.MANIFEST_NAME])

    def test_zip_link_is_rejected(self):
        info = zipfile.ZipInfo('a.txt')
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        with zipfile.ZipFile(self.package) as archive:
            entries = [(i, archive.read(i)) for i in archive.infolist() if i.filename != 'a.txt']
        with zipfile.ZipFile(self.package, 'w') as archive:
            for original, content in entries:
                archive.writestr(original, content)
            archive.writestr(info, 'new-a')
        with self.assertRaisesRegex(ValueError, '链接'):
            updater.apply_update(self.install, self.package)
        self.assertEqual(self.snapshot(), self.before)

    def test_existing_old_style_update_temporary_file_is_preserved(self):
        path = self.install / '.a.txt.testbox-update.tmp'
        path.write_bytes(b'user data')
        updater.apply_update(self.install, self.package)
        self.assertEqual(path.read_bytes(), b'user data')

    def test_matching_downloaded_manifest_and_package_can_update(self):
        # Package URL must agree with the manifest embedded in the ZIP.
        manifest = dict(self.manifest, package_url='https://example.test/new.zip')
        self.rewrite(manifest)
        def download(url, destination):
            if url.endswith('manifest.json'):
                destination.write_text(json.dumps(manifest), encoding='utf-8')
            else:
                destination.write_bytes(self.package.read_bytes())
        with patch.object(updater, 'download', side_effect=download):
            result = updater.download_and_apply(self.install, manifest_url='https://example.test/manifest.json',
                                               expected_component='cli')
        self.assertEqual(result['status'], 'updated')
        self.assertEqual(result['from_version'], '1.0.0')

    def test_download_up_to_date_does_not_download_archive(self):
        manifest = json.loads(self.old_manifest.read_text(encoding='utf-8'))
        with patch.object(updater, 'download', side_effect=lambda url, target: target.write_text(json.dumps(manifest))) as mocked:
            result = updater.download_and_apply(self.install, manifest_url='https://example.test/manifest.json',
                                               expected_component='cli')
        self.assertEqual(result, {'status': 'up_to_date', 'version': '1.0.0'})
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(self.snapshot(), self.before)

    def test_download_failure_does_not_touch_installation(self):
        with patch.object(updater, 'download', side_effect=OSError('offline')):
            with self.assertRaisesRegex(OSError, 'offline'):
                updater.download_and_apply(self.install, manifest_url='https://example.test/manifest.json')
        self.assertEqual(self.snapshot(), self.before)

    def test_wait_failure_does_not_touch_installation(self):
        with patch.object(updater, '_wait_for_pid', side_effect=TimeoutError('still running')):
            with self.assertRaisesRegex(TimeoutError, 'still running'):
                updater.apply_update(self.install, self.package, wait_for_pid=123)
        self.assertEqual(self.snapshot(), self.before)


if __name__ == '__main__':
    unittest.main()
