"""Bounded input staging and syntactic/resolved output boundaries."""
from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from testbox.core import workspace
from testbox.core.workspace import WorkspaceManager


FILE_SCHEMA = {"type": "string", "format": "file-path"}
ARRAY_SCHEMA = {"type": "array", "items": FILE_SCHEMA}


class TrackingReader(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.requests = []

    def read(self, size=-1):
        if size <= 0:
            raise AssertionError("input must use bounded reads")
        self.requests.append(size)
        return super().read(size)


class WorkspaceSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.manager = WorkspaceManager(self.root / "workspace", max_input_bytes=10, max_output_bytes=10)
        self.paths = self.manager.create("task")

    def input_file(self, name, content):
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_stream_hash_matches_source_and_staged_record_contract(self):
        content = b"0123456789"
        source = self.input_file("source.sql", content)
        original_open = Path.open
        reader = TrackingReader(content)

        def open_file(path, *args, **kwargs):
            if path == source and args and args[0] == "rb":
                return reader
            return original_open(path, *args, **kwargs)

        params = {"file": str(source), "other": "unchanged"}
        with patch.object(workspace, "INPUT_CHUNK_BYTES", 4), \
             patch.object(Path, "open", new=open_file), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")), \
             patch.object(workspace.shutil, "copy2", side_effect=AssertionError("copy must share hash stream")):
            staged, records = self.manager.stage_file_inputs({"properties": {"file": FILE_SCHEMA}}, params, self.paths)
        digest = hashlib.sha256(content).hexdigest()
        expected = self.paths.input / f"file-{digest[:12]}" / source.name
        self.assertGreater(len(reader.requests), 1)
        self.assertTrue(all(0 < request <= 4 for request in reader.requests))
        self.assertEqual(staged, {"file": str(expected), "other": "unchanged"})
        self.assertEqual(params, {"file": str(source), "other": "unchanged"})
        self.assertEqual(records, [{
            "parameter": "file", "source_path": str(source.resolve()),
            "staged_path": str(expected.relative_to(self.paths.root)),
            "size": len(content), "sha256": digest,
        }])
        self.assertEqual(hashlib.sha256(expected.read_bytes()).hexdigest(), digest)
        self.assertEqual(list(self.paths.input.glob(".staging-*")), [])

    def test_multiple_input_parameters_share_one_task_quota(self):
        first = self.input_file("first", b"123456")
        second = self.input_file("second", b"789012")
        schema = {"properties": {"first": FILE_SCHEMA, "second": FILE_SCHEMA}}
        with self.assertRaisesRegex(ValueError, "累计输入"):
            self.manager.stage_file_inputs(schema, {"first": str(first), "second": str(second)}, self.paths)

    def test_array_inputs_share_one_task_quota(self):
        first = self.input_file("first", b"123456")
        second = self.input_file("second", b"789012")
        with self.assertRaisesRegex(ValueError, "累计输入"):
            self.manager.stage_file_inputs({"properties": {"files": ARRAY_SCHEMA}}, {"files": [str(first), str(second)]}, self.paths)

    def test_single_and_array_inputs_share_one_task_quota(self):
        first = self.input_file("first", b"1234")
        second = self.input_file("second", b"5678")
        third = self.input_file("third", b"90ab")
        schema = {"properties": {"single": FILE_SCHEMA, "files": ARRAY_SCHEMA}}
        with self.assertRaisesRegex(ValueError, "累计输入"):
            self.manager.stage_file_inputs(schema, {"single": str(first), "files": [str(second), str(third)]}, self.paths)

    def test_duplicate_source_is_counted_for_each_staged_copy(self):
        source = self.input_file("source", b"123456")
        with self.assertRaisesRegex(ValueError, "累计输入"):
            self.manager.stage_file_inputs({"properties": {"files": ARRAY_SCHEMA}}, {"files": [str(source), str(source)]}, self.paths)

    def test_exact_combined_quota_preserves_array_order_and_names(self):
        sources = [self.input_file("first", b"1234"), self.input_file("second", b"567890")]
        staged, records = self.manager.stage_file_inputs(
            {"properties": {"files": ARRAY_SCHEMA}}, {"files": [str(path) for path in sources]}, self.paths,
        )
        self.assertEqual(sum(record["size"] for record in records), 10)
        for index, (source, destination, record) in enumerate(zip(sources, staged["files"], records), 1):
            self.assertIn(f"files-{index}-", destination)
            self.assertEqual(Path(destination).read_bytes(), source.read_bytes())
            self.assertEqual(record["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(record["source_path"], str(source))

    def test_task_quota_resets_for_next_task(self):
        source = self.input_file("source", b"1234567890")
        schema = {"properties": {"file": FILE_SCHEMA}}
        for paths in (self.paths, self.manager.create("other-task")):
            _, records = self.manager.stage_file_inputs(schema, {"file": str(source)}, paths)
            self.assertEqual(records[0]["size"], 10)

    def test_growth_after_stat_is_limited_and_partial_temp_removed(self):
        source = self.input_file("source", b"123")
        original_open = Path.open
        reader = TrackingReader(b"12345678901")

        def open_file(path, *args, **kwargs):
            if path == source and args and args[0] == "rb":
                return reader
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", new=open_file), patch.object(workspace, "INPUT_CHUNK_BYTES", 3):
            with self.assertRaisesRegex(ValueError, "累计输入"):
                self.manager.stage_file_inputs({"properties": {"file": FILE_SCHEMA}}, {"file": str(source)}, self.paths)
        self.assertTrue(all(request <= 3 for request in reader.requests))
        self.assertEqual(list(self.paths.input.iterdir()), [])

    def test_growth_of_second_input_observes_remaining_total(self):
        first = self.input_file("first", b"12345678")
        second = self.input_file("second", b"9")
        original_open = Path.open

        def open_file(path, *args, **kwargs):
            if path == second and args and args[0] == "rb":
                return TrackingReader(b"901")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", new=open_file):
            with self.assertRaisesRegex(ValueError, "累计输入"):
                self.manager.stage_file_inputs(
                    {"properties": {"first": FILE_SCHEMA, "second": FILE_SCHEMA}},
                    {"first": str(first), "second": str(second)}, self.paths,
                )
        self.assertEqual(list(self.paths.input.glob(".staging-*")), [])
        self.assertEqual(len(list(self.paths.input.iterdir())), 1)

    def test_zero_length_input_at_zero_quota_is_valid(self):
        source = self.input_file("empty", b"")
        self.manager.max_input_bytes = 0
        _, records = self.manager.stage_file_inputs({"properties": {"file": FILE_SCHEMA}}, {"file": str(source)}, self.paths)
        self.assertEqual(records[0]["sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(records[0]["size"], 0)

    def test_missing_input_rejected(self):
        with self.assertRaisesRegex(ValueError, "输入文件不存在"):
            self.manager.stage_file_inputs({"properties": {"file": FILE_SCHEMA}}, {"file": str(self.root / "missing")}, self.paths)

    def test_non_file_parameters_remain_unchanged(self):
        params = {"text": "not a file", "empty": []}
        staged, records = self.manager.stage_file_inputs({"properties": {"text": {"type": "string"}, "empty": ARRAY_SCHEMA}}, params, self.paths)
        self.assertEqual(staged, params)
        self.assertEqual(records, [])

    def test_output_syntax_rejects_absolute_and_parent_paths_even_inside_root(self):
        (self.paths.output / "ok.txt").write_bytes(b"ok")
        (self.paths.output / "nested").mkdir()
        invalid = [
            str(self.paths.output / "ok.txt"), "nested/../ok.txt", "nested\\..\\ok.txt",
            "../output/ok.txt", "..\\output\\ok.txt", "C:\\file", "C:/file", "C:file",
            "\\\\server\\share\\file", "//server/share/file", "\\file", "ok.txt:stream", "", "nul\x00file",
        ]
        for name in invalid:
            with self.subTest(name=name):
                self.assertEqual(self.manager.validate_outputs(self.paths, [name]), "INVALID_OUTPUT_PATH")
                with self.assertRaisesRegex(ValueError, "输出路径不合法"):
                    self.manager.resolve_output(self.paths.root, name)
                export = self.root / "export.txt"
                with self.assertRaisesRegex(ValueError, "输出路径不合法"):
                    self.manager.export(self.paths.root, name, export)
                self.assertFalse(export.exists())
                archive = self.root / "export.zip"
                with self.assertRaisesRegex(ValueError, "输出路径不合法"):
                    self.manager.export_archive(self.paths.root, ["ok.txt", name], archive)
                self.assertFalse(archive.exists())

    def test_output_symlink_escape_rejected_by_validation_and_exports(self):
        outside = self.input_file("outside", b"secret")
        (self.paths.output / "linked").symlink_to(outside)
        self.assertEqual(self.manager.validate_outputs(self.paths, ["linked"]), "INVALID_OUTPUT_PATH")
        with self.assertRaisesRegex(ValueError, "输出路径不合法"):
            self.manager.resolve_output(self.paths.root, "linked")
        with self.assertRaises(ValueError):
            self.manager.export_archive(self.paths.root, ["linked"], self.root / "outside.zip")

    def test_output_size_is_cumulative_with_exact_boundary(self):
        (self.paths.output / "one").write_bytes(b"1234")
        (self.paths.output / "two").write_bytes(b"567890")
        self.assertIsNone(self.manager.validate_outputs(self.paths, ["one", "two"]))
        (self.paths.output / "three").write_bytes(b"x")
        self.assertEqual(self.manager.validate_outputs(self.paths, ["one", "two", "three"]), "OUTPUT_TOO_LARGE")

    def test_missing_output_and_empty_archive_contract(self):
        self.assertEqual(self.manager.validate_outputs(self.paths, ["missing"]), "MISSING_OUTPUT_FILE")
        with self.assertRaisesRegex(ValueError, "输出文件不存在"):
            self.manager.resolve_output(self.paths.root, "missing")
        with self.assertRaisesRegex(ValueError, "没有可导出"):
            self.manager.export_archive(self.paths.root, [], self.root / "empty.zip")

    def test_nested_outputs_with_either_separator_resolve_and_export(self):
        (self.paths.output / "nested").mkdir()
        source = self.paths.output / "nested/file.txt"
        source.write_bytes(b"result")
        for name in ("nested/file.txt", "nested\\file.txt"):
            with self.subTest(name=name):
                self.assertIsNone(self.manager.validate_outputs(self.paths, [name]))
                self.assertEqual(self.manager.resolve_output(self.paths.root, name), source.resolve())
                export = self.root / "export.txt"
                self.manager.export(self.paths.root, name, export)
                self.assertEqual(export.read_bytes(), b"result")
                archive = self.root / "export.zip"
                self.manager.export_archive(self.paths.root, [name], archive)
                with zipfile.ZipFile(archive) as package:
                    self.assertEqual(package.namelist(), ["nested/file.txt"])
                    self.assertEqual(package.read("nested/file.txt"), b"result")


if __name__ == "__main__":
    unittest.main()
