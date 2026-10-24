"""Manifest-driven catalog and desktop preference regressions without Qt."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from testbox.core.manifest import Manifest
from testbox.core.runtime import Runtime
from testbox.gui_catalog import CatalogPreferences


def make_catalog_plugin(root, name="sample-plugin", commands=("sample.run", "sample.inspect"),
                        category="custom-group", group_label="自定义工具", version="1.0.0", labels=True):
    directory = Path(root) / name
    directory.mkdir(parents=True, exist_ok=True)
    display = "display_name: 新工具\n" if labels else ""
    group = f"category_display_name: {group_label}\n" if labels else ""
    command_yaml = "".join(f"  - name: {command}\n" +
                           (f"    display_name: 操作{index}\n" if labels else "") +
                           "    description: 命令说明\n    input_schema: schema.json\n"
                           for index, command in enumerate(commands))
    (directory / "manifest.yaml").write_text(
        f"schema_version: 1\nname: {name}\n{display}version: {version}\n"
        f"description: 插件说明\ncategory: {category}\n{group}"
        "core_compatibility: '>=1.0.0'\nentry: main:Plugin\ncommands:\n" + command_yaml +
        "capabilities:\n  concurrency: true\n  network: false\n"
        "  filesystem: output-only\n  resources: []\n", encoding="utf-8")
    (directory / "schema.json").write_text('{"type":"object","properties":{}}', encoding="utf-8")
    (directory / "main.py").write_text(
        "from testbox.sdk import Result\nclass Plugin:\n"
        "    def init(self, context): pass\n"
        "    def execute(self, command, params): return Result('success', 'fixture', {}, [], [])\n"
        "    def destroy(self): pass\n", encoding="utf-8")
    return directory


class CatalogMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def runtime(self):
        runtime = Runtime(self.root)
        self.addCleanup(runtime.close)
        return runtime

    def test_labels_and_groups_match_yaml_fallback_and_legacy(self):
        for labels in (True, False):
            directory = make_catalog_plugin(self.root / "plugins", labels=labels)
            loaded = Manifest.load(directory / "manifest.yaml")
            with patch("testbox.core.manifest.yaml", None):
                self.assertEqual(Manifest.load(directory / "manifest.yaml"), loaded)
            self.assertEqual(loaded.display_name, "新工具" if labels else "sample-plugin")
            self.assertEqual(loaded.category_display_name, "自定义工具" if labels else "custom-group")
            self.assertEqual(loaded.commands[0].display_name, "操作0" if labels else "sample.run")
        directory = make_catalog_plugin(self.root / "plugins", labels=False, category='x' * 101)
        self.assertEqual(Manifest.load(directory / 'manifest.yaml').category_display_name, 'x' * 101)

    def test_invalid_metadata_isolated_while_blank_falls_back(self):
        directory = make_catalog_plugin(self.root / "plugins")
        path = directory / "manifest.yaml"
        original = path.read_text(encoding="utf-8")
        for field, old in (("display_name", "新工具"), ("category_display_name", "自定义工具")):
            for invalid in ("false", "[]", "123", "12.5", "null", "x" * 101):
                path.write_text(original.replace(f"{field}: {old}", f"{field}: {invalid}"), encoding="utf-8")
                runtime = self.runtime()
                self.assertEqual(runtime.list_catalog_entries(), [])
                self.assertIn(field, runtime.list_unavailable_plugins()[0]["reason"])
                with patch("testbox.core.manifest.yaml", None):
                    with self.assertRaisesRegex(ValueError, field):
                        Manifest.load(path)
        path.write_text(original.replace("display_name: 新工具", "display_name: '   '")
                        .replace("category_display_name: 自定义工具", "category_display_name: ''")
                        .replace("display_name: 操作0", "display_name: ''"), encoding="utf-8")
        entry = self.runtime().list_catalog_entries()[0]
        self.assertEqual((entry['plugin_display_name'], entry['category_display_name'], entry['command_display_name']),
                         ('sample-plugin', 'custom-group', 'sample.run'))
        path.write_text(original.replace("display_name: 操作0", "display_name: []"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "display_name"):
            Manifest.load(path)

    def test_projection_never_imports_plugin_or_creates_tasks(self):
        directory = make_catalog_plugin(self.root / "plugins")
        (directory / "main.py").write_text("raise RuntimeError('must not import')", encoding="utf-8")
        runtime = self.runtime()
        with patch("importlib.import_module", side_effect=AssertionError("plugin import")):
            entries = runtime.list_catalog_entries()
        self.assertEqual([item['command_name'] for item in entries], ['sample.run', 'sample.inspect'])
        self.assertEqual(entries[0]['category_display_name'], '自定义工具')
        self.assertEqual(entries[0]['source'], 'local')
        entries[0]['capabilities']['resources'].append('mutated')
        self.assertEqual(runtime.get_command('sample.run').capabilities['resources'], [])
        self.assertEqual(runtime.list_tasks(), [])

    def test_partial_override_reports_each_active_version_and_source(self):
        runtime = self.runtime()
        bundled, user = self.root / "bundled", self.root / "plugins"
        make_catalog_plugin(bundled)
        make_catalog_plugin(user, commands=("sample.run",), version="2.0.0")
        runtime.bundled_plugins_dir = bundled
        runtime.manager.plugins_dirs = [user, bundled]
        runtime.reload_plugins()
        entries = {item['command_name']: item for item in runtime.list_catalog_entries()}
        self.assertEqual((entries['sample.run']['source'], entries['sample.run']['plugin_version']), ('user', '2.0.0'))
        self.assertEqual((entries['sample.inspect']['source'], entries['sample.inspect']['plugin_version']), ('bundled', '1.0.0'))
        self.assertEqual(len(entries), 2)
        runtime.uninstall_plugin('sample-plugin')
        self.assertEqual({item['source'] for item in runtime.list_catalog_entries()}, {'bundled'})

    def test_legacy_plugin_package_and_preview_remain_compatible(self):
        directory = make_catalog_plugin(self.root / "source", labels=False)
        runtime = self.runtime()
        archive = runtime.package_plugin(directory, self.root / "sample.zip")
        preview = runtime.preview_plugin_install(archive)
        self.assertEqual(preview['category_display_name'], 'custom-group')
        self.assertEqual(runtime.list_catalog_entries(), [])
        runtime.install_plugin(archive, trust_code=True)
        self.assertEqual(runtime.inspect_plugin('sample-plugin')['display_name'], 'sample-plugin')
        self.assertEqual(len(runtime.list_catalog_entries()), 2)
        _, result = runtime.run('sample.run', {})
        self.assertEqual(result.status, 'success', result.message)


class MemorySettings:
    def __init__(self):
        self.values = {}
        self.failed = False

    def value(self, key, default):
        return self.values.get(key, default)

    def setValue(self, key, value):
        if not self.failed:
            self.values[key] = value

    def sync(self):
        pass

    def status(self):
        return int(self.failed)


class CatalogPreferenceTests(unittest.TestCase):
    def test_roundtrip_deduplicates_and_limits_recent_without_parameters(self):
        settings = MemorySettings()
        settings.setValue('appearance/theme', 'dark')
        preferences = CatalogPreferences(settings)
        preferences.toggle_favorite(('sample-plugin', 'sample.run'))
        for index in range(15):
            preferences.record_recent(('sample-plugin', f'sample.run{index}'))
        preferences.record_recent(('sample-plugin', 'sample.run5'))
        restored = CatalogPreferences(settings)
        self.assertEqual(restored.favorites, [('sample-plugin', 'sample.run')])
        self.assertEqual(len(restored.recent), 12)
        self.assertEqual(restored.recent[0], ('sample-plugin', 'sample.run5'))
        self.assertEqual(len(set(restored.recent)), 12)
        self.assertEqual(settings.values['appearance/theme'], 'dark')
        for key in ('favorites', 'recent'):
            self.assertTrue(all(set(item) == {'plugin_name', 'command_name'}
                                for item in json.loads(settings.values[f'catalog/{key}'])))

    def test_corrupt_and_partial_preferences_are_nonblocking(self):
        settings = MemorySettings()
        settings.values['catalog/favorites'] = 'broken'
        preferences = CatalogPreferences(settings)
        self.assertEqual(preferences.favorites, [])
        self.assertTrue(preferences.warning)
        good = {'plugin_name': 'sample-plugin', 'command_name': 'sample.run'}
        settings.values['catalog/favorites'] = json.dumps([None, good, good, {**good, 'params': 'secret'}])
        preferences.reload()
        self.assertEqual(preferences.favorites, [('sample-plugin', 'sample.run')])
        self.assertTrue(preferences.warning)

    def test_failed_save_retains_memory_and_retries_both_keys(self):
        settings = MemorySettings()
        preferences = CatalogPreferences(settings)
        settings.failed = True
        identity = ('sample-plugin', 'sample.run')
        preferences.toggle_favorite(identity)
        preferences.record_recent(identity)
        preferences.reload()
        self.assertEqual(preferences.favorites, [identity])
        self.assertEqual(preferences.recent, [identity])
        self.assertIn('下次启动', preferences.warning)
        settings.failed = False
        preferences.record_recent(identity)
        restored = CatalogPreferences(settings)
        self.assertEqual(restored.favorites, [identity])
        self.assertEqual(restored.recent, [identity])
        self.assertFalse(preferences.warning)


if __name__ == '__main__':
    unittest.main()
