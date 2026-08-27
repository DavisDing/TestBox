from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from testbox.updater import MANIFEST_NAME, apply_update, create_update_package


class UpdaterTests(unittest.TestCase):
    def test_full_and_incremental_update_preserve_unmanaged_files(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            release_one = temp / "release-one"
            release_one.mkdir()
            (release_one / "app.txt").write_text("one", encoding="utf-8")
            (release_one / "remove.txt").write_text("remove", encoding="utf-8")
            (release_one / "nested").mkdir()
            (release_one / "nested" / "keep.txt").write_text("keep", encoding="utf-8")
            package_one = temp / "update-v1.0.0.zip"
            manifest_one_path = temp / "manifest-v1.0.0.json"
            manifest_one = create_update_package(release_one, version="1.0.0", output=package_one, manifest_output=manifest_one_path)

            install = temp / "install"
            install.mkdir()
            (install / "user-file.txt").write_text("do not delete", encoding="utf-8")
            self.assertEqual(apply_update(install, package_one)["version"], "1.0.0")
            self.assertEqual((install / "app.txt").read_text(encoding="utf-8"), "one")
            self.assertTrue((install / "remove.txt").exists())

            release_two = temp / "release-two"
            release_two.mkdir()
            (release_two / "app.txt").write_text("two", encoding="utf-8")
            (release_two / "nested").mkdir()
            (release_two / "nested" / "keep.txt").write_text("keep", encoding="utf-8")
            (release_two / "new.txt").write_text("new", encoding="utf-8")
            package_two = temp / "update-v1.0.1.zip"
            manifest_two_path = temp / "manifest-v1.0.1.json"
            manifest_two = create_update_package(
                release_two,
                version="1.0.1",
                output=package_two,
                manifest_output=manifest_two_path,
                previous_manifest=manifest_one_path,
            )
            self.assertEqual(manifest_two["base_version"], "1.0.0")
            self.assertEqual(manifest_two["changed_files"], ["app.txt", "new.txt"])
            self.assertEqual(manifest_two["deleted_files"], ["remove.txt"])
            result = apply_update(install, package_two)
            self.assertEqual(result["version"], "1.0.1")
            self.assertEqual((install / "app.txt").read_text(encoding="utf-8"), "two")
            self.assertEqual((install / "new.txt").read_text(encoding="utf-8"), "new")
            self.assertFalse((install / "remove.txt").exists())
            self.assertEqual((install / "nested" / "keep.txt").read_text(encoding="utf-8"), "keep")
            self.assertEqual((install / "user-file.txt").read_text(encoding="utf-8"), "do not delete")
            installed_manifest = json.loads((install / MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(installed_manifest["version"], "1.0.1")

    def test_incremental_update_requires_base_version(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            release = temp / "release"
            release.mkdir()
            (release / "app.txt").write_text("new", encoding="utf-8")
            package = temp / "update.zip"
            manifest = temp / "manifest.json"
            previous_manifest = temp / "previous-manifest.json"
            previous_manifest.write_text(json.dumps({
                "schema_version": 1,
                "version": "1.0.0",
                "files": [{"path": "old.txt", "size": 3, "sha256": "0" * 64}],
            }), encoding="utf-8")
            create_update_package(release, version="1.0.1", output=package, manifest_output=manifest, previous_manifest=previous_manifest)
            with self.assertRaisesRegex(ValueError, "没有版本清单"):
                apply_update(temp / "empty-install", package)

    def test_full_update_creates_missing_install_root(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            release = temp / "release"
            release.mkdir()
            (release / "app.txt").write_text("first", encoding="utf-8")
            package = temp / "update.zip"
            manifest = temp / "manifest.json"
            create_update_package(release, version="1.0.0", output=package, manifest_output=manifest)
            install = temp / "new-install"
            apply_update(install, package)
            self.assertEqual((install / "app.txt").read_text(encoding="utf-8"), "first")

    def test_update_rejects_zip_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            package = temp / "bad.zip"
            manifest = {
                "schema_version": 1,
                "version": "1.0.0",
                "base_version": None,
                "package": package.name,
                "package_url": None,
                "files": [{"path": "../escape.txt", "size": 1, "sha256": "0" * 64}],
                "changed_files": ["../escape.txt"],
                "deleted_files": [],
            }
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr(MANIFEST_NAME, json.dumps(manifest))
                archive.writestr("../escape.txt", "x")
            with self.assertRaisesRegex(ValueError, "非法更新文件路径"):
                apply_update(temp / "install", package)


if __name__ == "__main__":
    unittest.main()
