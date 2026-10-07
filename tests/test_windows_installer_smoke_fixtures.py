"""Cross-platform synthetic/source tests, not Windows native installer tests."""
from __future__ import annotations

import copy
import hashlib
import json
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import windows_smoke_fixtures as fixtures
from testbox import updater


class WindowsInstallerSmokeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.install = self.root / "installed"
        self.install.mkdir()
        self.payloads = {
            "TestBox-CLI.exe": b"synthetic executable placeholder; not a native binary",
            "resources/unchanged.dat": b"unchanged resource",
            "resources/replaced.txt": b"old text",
            "resources/deleted.txt": b"remove me",
        }
        for relative, payload in self.payloads.items():
            path = self.install / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        self.installed = {
            "schema_version": updater.MANIFEST_SCHEMA_VERSION,
            "component": "cli",
            "version": "1.0.0",
            "base_version": None,
            "package": "old.zip",
            "package_url": "https://example.invalid/old.zip",
            "files": [{"path": relative, "size": len(payload),
                       "sha256": hashlib.sha256(payload).hexdigest()}
                      for relative, payload in sorted(self.payloads.items())],
            "changed_files": sorted(self.payloads),
            "deleted_files": [],
        }
        self.write_installed_manifest()
        (self.install / "user.txt").write_bytes(b"unmanaged user data")
        (self.install / "TestBox-CLI-Updater.exe").write_bytes(b"protected updater placeholder")
        self.output = self.root / "fixture.zip"
        self.replacements = {"resources/replaced.txt": b"new text", "nested/added.txt": b"added text"}
        self.deleted = ["resources/deleted.txt"]

    def write_installed_manifest(self):
        (self.install / updater.MANIFEST_NAME).write_text(json.dumps(self.installed), encoding="utf-8")

    def snapshot(self):
        return {path.relative_to(self.install).as_posix(): path.read_bytes()
                for path in self.install.rglob("*") if path.is_file()}

    def build(self, **overrides):
        arguments = {"version": "1.0.1", "replacements": self.replacements, "deleted": self.deleted}
        arguments.update(overrides)
        return fixtures.build_delta_fixture(self.installed, self.output, **arguments)

    def rewrite_archive(self, source, destination, *, mutate=None, extra=(), payloads=None):
        with zipfile.ZipFile(source) as archive:
            entries = [(info, archive.read(info)) for info in archive.infolist()]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for info, payload in entries:
                    if info.filename == updater.MANIFEST_NAME and mutate:
                        manifest = json.loads(payload)
                        mutate(manifest)
                        payload = json.dumps(manifest).encode("utf-8")
                    if payloads and info.filename in payloads:
                        payload = payloads[info.filename]
                    archive.writestr(info, payload)
                for name, payload in extra:
                    archive.writestr(name, payload)

    def assert_build_rejected(self, **arguments):
        self.output.write_bytes(b"existing output must survive")
        before = copy.deepcopy(self.installed)
        with self.assertRaises(ValueError):
            self.build(**arguments)
        self.assertEqual(self.output.read_bytes(), b"existing output must survive")
        self.assertEqual(self.installed, before)
        self.assertFalse(list(self.root.glob(".fixture.zip.*.tmp")))

    def test_fixture_applies_add_replace_delete_and_preserves_unmanaged_and_unchanged(self):
        for component in ("cli", "gui"):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as temporary:
                install = Path(temporary) / "install"
                install.mkdir()
                for relative, payload in self.snapshot().items():
                    path = install / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)
                old = copy.deepcopy(self.installed)
                old["component"] = component
                (install / updater.MANIFEST_NAME).write_text(json.dumps(old), encoding="utf-8")
                manifest = fixtures.build_delta_fixture(
                    old, self.output, version="1.0.1", replacements=self.replacements, deleted=self.deleted)
                result = updater.apply_update(install, self.output, expected_component=component)
                self.assertEqual(result, {"version": "1.0.1", "changed": sorted(self.replacements),
                                          "deleted": self.deleted})
                for relative, payload in self.replacements.items():
                    self.assertEqual((install / relative).read_bytes(), payload)
                self.assertFalse((install / self.deleted[0]).exists())
                for relative in ("TestBox-CLI.exe", "resources/unchanged.dat", "user.txt",
                                 "TestBox-CLI-Updater.exe"):
                    self.assertEqual((install / relative).read_bytes(), (self.install / relative).read_bytes())
                self.assertEqual(json.loads((install / updater.MANIFEST_NAME).read_bytes()), manifest)

    def test_complete_metadata_is_preserved_without_bundle_payload_or_input_mutation(self):
        # A large onedir file need not exist or be read to build this small ZIP.
        self.installed["files"][0]["size"] = 500_000_000
        self.installed["files"][0]["extra"] = {"provenance": ["retained"]}
        before = copy.deepcopy(self.installed)
        replacements_before = copy.deepcopy(self.replacements)
        deleted_before = list(self.deleted)
        manifest = self.build()
        self.assertEqual(self.installed, before)
        self.assertEqual(self.replacements, replacements_before)
        self.assertEqual(self.deleted, deleted_before)
        self.assertEqual(manifest["base_version"], before["version"])
        self.assertEqual(manifest["component"], before["component"])
        self.assertEqual(manifest["package"], self.output.name)
        self.assertIsNone(manifest["package_url"])
        files = {item["path"]: item for item in manifest["files"]}
        for item in before["files"]:
            if item["path"] not in self.replacements and item["path"] not in self.deleted:
                self.assertEqual(files[item["path"]], item)
        for relative, payload in self.replacements.items():
            self.assertEqual(files[relative], {"path": relative, "size": len(payload),
                                             "sha256": hashlib.sha256(payload).hexdigest()})
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), {updater.MANIFEST_NAME, *self.replacements})
            self.assertEqual(json.loads(archive.read(updater.MANIFEST_NAME)), manifest)
            for relative, payload in self.replacements.items():
                self.assertEqual(archive.read(relative), payload)
        self.assertLess(self.output.stat().st_size, 4096)
        files["TestBox-CLI.exe"]["extra"]["provenance"].append("caller modified return")
        self.assertEqual(self.installed, before)

    def test_wrong_base_and_component_are_rejected_without_install_mutation(self):
        self.build()
        for key, value in (("base_version", "0.9.9"), ("component", "gui")):
            with self.subTest(key=key):
                bad = self.root / f"wrong-{key}.zip"
                self.rewrite_archive(self.output, bad, mutate=lambda manifest: manifest.update({key: value}))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    updater.apply_update(self.install, bad)
                self.assertEqual(self.snapshot(), before)
        with self.assertRaisesRegex(ValueError, "组件不匹配"):
            updater.apply_update(self.install, self.output, expected_component="gui")
        self.assertEqual(self.snapshot(), before)

    def test_corrupt_payload_changes_one_byte_keeps_manifest_and_fails_real_hash_check(self):
        self.build()
        before = self.snapshot()
        original_bytes = self.output.read_bytes()
        bad = self.root / "corrupt.zip"
        fixtures.corrupt_payload(self.output, bad)
        self.assertEqual(self.output.read_bytes(), original_bytes)
        with zipfile.ZipFile(self.output) as original, zipfile.ZipFile(bad) as corrupted:
            self.assertIsNone(corrupted.testzip())
            self.assertEqual(original.namelist(), corrupted.namelist())
            self.assertEqual(original.read(updater.MANIFEST_NAME), corrupted.read(updater.MANIFEST_NAME))
            differences = 0
            for relative in self.replacements:
                old, new = original.read(relative), corrupted.read(relative)
                self.assertEqual(len(old), len(new))
                differences += sum(left != right for left, right in zip(old, new))
            self.assertEqual(differences, 1)
        with self.assertRaisesRegex(ValueError, "更新文件校验失败"):
            updater.apply_update(self.install, bad)
        self.assertEqual(self.snapshot(), before)
        updater.apply_update(self.install, self.output)
        self.assertEqual((self.install / "nested/added.txt").read_bytes(), b"added text")

    def test_versions_and_empty_changes_are_rejected(self):
        for version in ("", "  ", "1.0.0", None, 123):
            with self.subTest(version=version):
                self.assert_build_rejected(version=version)
        self.assert_build_rejected(replacements={}, deleted=[])

    def test_unsafe_and_protected_replacement_paths_are_rejected(self):
        paths = ("../escape", "/absolute", "C:/escape", "C:relative", "a\\b", "a//b", "a/",
                 "a/../b", "a/./b", "NUL.txt", "dir/CON", "a:stream", "a?b", "trailing.",
                 "a\x00b", "a ", updater.MANIFEST_NAME, "UPDATE-MANIFEST.JSON",
                 "TestBox-Updater.exe", "TestBox-CLI-Updater.exe", "dir/testbox-gui-updater.EXE")
        for relative in paths:
            with self.subTest(path=relative):
                self.assert_build_rejected(replacements={relative: b"payload"})

    def test_deletions_must_be_exact_old_managed_paths(self):
        for relative in ("user.txt", "missing.txt", "RESOURCES/deleted.txt", "../escape",
                         updater.MANIFEST_NAME, "TestBox-CLI-Updater.exe"):
            with self.subTest(path=relative):
                self.assert_build_rejected(deleted=[relative])
        self.assert_build_rejected(deleted=self.deleted * 2)
        self.assert_build_rejected(replacements={self.deleted[0]: b"new"})

    def test_invalid_payloads_and_manifest_collisions_are_rejected(self):
        for payload in ("text", bytearray(b"bytes"), None):
            with self.subTest(payload=payload):
                self.assert_build_rejected(replacements={"new.txt": payload})
        self.assert_build_rejected(replacements={"new.txt": b"a", "NEW.TXT": b"b"})
        self.assert_build_rejected(replacements={"new": b"a", "new/file": b"b"})
        self.assert_build_rejected(replacements={"RESOURCES/unchanged.dat": b"b"})
        self.assert_build_rejected(replacements=[], deleted=self.deleted)
        self.assert_build_rejected(deleted="resources/deleted.txt")
        self.installed["component"] = "unknown"
        self.assert_build_rejected()

    def test_zero_byte_and_deletion_only_fixtures_apply_but_cannot_be_corrupted(self):
        for replacements in ({"nested/empty.txt": b""}, {}):
            with self.subTest(replacements=replacements):
                # Reset the install baseline between actual updates.
                for relative, payload in self.payloads.items():
                    (self.install / relative).write_bytes(payload)
                self.write_installed_manifest()
                self.build(replacements=replacements)
                bad = self.root / "bad.zip"
                bad.write_bytes(b"existing destination")
                with self.assertRaisesRegex(ValueError, "non-empty"):
                    fixtures.corrupt_payload(self.output, bad)
                self.assertEqual(bad.read_bytes(), b"existing destination")
                updater.apply_update(self.install, self.output)
                self.assertFalse((self.install / self.deleted[0]).exists())
                for relative in replacements:
                    self.assertEqual((self.install / relative).read_bytes(), b"")

    def test_builder_zip_write_and_rename_failures_preserve_existing_output(self):
        for method in ("write", "rename"):
            with self.subTest(method=method):
                self.output.write_bytes(b"existing output")
                target = (patch.object(fixtures.zipfile.ZipFile, "writestr", side_effect=OSError("write failure"))
                          if method == "write" else
                          patch.object(fixtures.Path, "replace", side_effect=OSError("rename failure")))
                with target, self.assertRaises(OSError):
                    self.build()
                self.assertEqual(self.output.read_bytes(), b"existing output")
                self.assertFalse(list(self.root.glob(".fixture.zip.*.tmp")))

    def test_corruptor_rejects_invalid_source_and_preserves_destination(self):
        self.build()
        invalid = self.root / "invalid.zip"
        destination = self.root / "destination.zip"
        source_bytes = self.output.read_bytes()
        cases = (
            {"extra": [("unlisted.txt", b"extra")]},
            {"extra": [("nested/added.txt", b"duplicate")]},
            {"extra": [("../escape", b"bad")]},
            {"payloads": {"nested/added.txt": b"wrong hash"}},
            {"payloads": {"nested/added.txt": b"wrong size and hash"}},
            {"mutate": lambda manifest: manifest.update(base_version=None)},
            {"mutate": lambda manifest: manifest.update(version=manifest["base_version"])},
            {"mutate": lambda manifest: manifest.update(component="unknown")},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.rewrite_archive(self.output, invalid, **arguments)
                destination.write_bytes(b"existing destination")
                with self.assertRaises(ValueError):
                    fixtures.corrupt_payload(invalid, destination)
                self.assertEqual(destination.read_bytes(), b"existing destination")
                self.assertEqual(self.output.read_bytes(), source_bytes)
        for relative, mode in (("link", stat.S_IFLNK), ("directory/", stat.S_IFDIR)):
            info = zipfile.ZipInfo(relative)
            info.create_system = 3
            info.external_attr = (mode | 0o777) << 16
            self.rewrite_archive(self.output, invalid, extra=[(info, b"target")])
            with self.assertRaises(ValueError):
                fixtures.corrupt_payload(invalid, destination)
        with zipfile.ZipFile(invalid, "w") as archive:
            archive.writestr("file.txt", b"no manifest")
        with self.assertRaises(ValueError):
            fixtures.corrupt_payload(invalid, destination)
        self.assertEqual(destination.read_bytes(), b"existing destination")

    def test_corruptor_rejects_same_destination_and_cleans_failed_write(self):
        self.build()
        source_bytes = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, "differ"):
            fixtures.corrupt_payload(self.output, self.output)
        self.assertEqual(self.output.read_bytes(), source_bytes)
        destination = self.root / "destination.zip"
        destination.write_bytes(b"existing destination")
        with patch.object(fixtures.zipfile.ZipFile, "writestr", side_effect=OSError("write failure")):
            with self.assertRaises(OSError):
                fixtures.corrupt_payload(self.output, destination)
        self.assertEqual(destination.read_bytes(), b"existing destination")
        self.assertEqual(self.output.read_bytes(), source_bytes)
        self.assertFalse(list(self.root.glob(".destination.zip.*.tmp")))


if __name__ == "__main__":
    unittest.main()
