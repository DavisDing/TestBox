"""Opt-in pre-staging policies preserve original identity before snapshots."""
from pathlib import Path
import shutil
from unittest.mock import patch
import tempfile
import unittest

from testbox.core.runtime import Runtime
from testbox.core.workspace import WorkspaceManager


class OfficeInputPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.manager=WorkspaceManager(self.root/'workspace',max_input_bytes=1000,max_output_bytes=1000)
        self.paths=self.manager.create('test')
        self.policy={'reject_symlinks':True,'unique_sources':True,'max_files':100}
        self.schema={'properties':{'inputs':{'type':'array','items':{'type':'string','format':'file-path'},'x-input-policy':self.policy}}}

    def file(self,name,data=b'TEST DATA ONLY'):
        p=self.root/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(data)
        return p

    def test_repeated_original_rejected_before_any_staging(self):
        p=self.file('a.xls')
        with self.assertRaisesRegex(ValueError,'重复来源'):
            self.manager.stage_file_inputs(self.schema,{'inputs':[str(p),str(p.parent/'sub/../a.xls')]},self.paths)
        self.assertEqual(list(self.paths.input.iterdir()),[])

    def test_selected_symlink_rejected_before_any_staging(self):
        target=self.file('a.doc')
        link=self.root/'link.doc'
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'符号链接'):
            self.manager.stage_file_inputs(self.schema,{'inputs':[str(link)]},self.paths)
        self.assertEqual(list(self.paths.input.iterdir()),[])

    def test_same_name_identical_bytes_different_sources_are_not_duplicates(self):
        a,b=self.file('one/a.xls'),self.file('two/a.xls')
        staged,records=self.manager.stage_file_inputs(self.schema,{'inputs':[str(a),str(b)]},self.paths)
        self.assertNotEqual(staged['inputs'][0],staged['inputs'][1])
        self.assertEqual(len(records),2)
        self.assertEqual(a.read_bytes(),b'TEST DATA ONLY')

    def test_no_policy_keeps_existing_link_and_repeat_behavior(self):
        p=self.file('a.xls')
        link=self.root/'link.xls';link.symlink_to(p)
        schema={'properties':{'inputs':{'type':'array','items':{'type':'string','format':'file-path'}}}}
        staged,records=self.manager.stage_file_inputs(schema,{'inputs':[str(link),str(p)]},self.paths)
        self.assertEqual(len(staged['inputs']),2)
        self.assertEqual(len(records),2)

    def test_maximum_files_checked_before_any_staging(self):
        p=self.file('a.ppt')
        with self.assertRaisesRegex(ValueError,'最多支持'):
            self.manager.stage_file_inputs(self.schema,{'inputs':[str(p)]*101},self.paths)
        self.assertEqual(list(self.paths.input.iterdir()),[])


class OfficeRuntimeInputPolicyTests(unittest.TestCase):
    """The real manifest must reject original links/repeats before Host spawn."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        project = Path(__file__).resolve().parents[1]
        shutil.copytree(
            project / "plugins" / "office-convert",
            self.root / "plugins" / "office-convert",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        self.runtime = Runtime(self.root)
        self.addCleanup(self.runtime.close)
        self.source = self.root / "legacy.xls"
        # These tests exercise source identity, not a synthetic conversion.
        self.source.write_bytes(b"TEST DATA ONLY")

    def assert_rejected_before_host(self, params, message):
        before = set(self.runtime.workspace_dir.iterdir())
        with patch.object(self.runtime.process_runner, "run") as host:
            with self.assertRaisesRegex(ValueError, message):
                self.runtime.run("office.convert", params)
            host.assert_not_called()
        self.assertEqual(self.runtime.count_tasks(), 0)
        self.assertEqual(set(self.runtime.workspace_dir.iterdir()), before)
        self.assertEqual(self.source.read_bytes(), b"TEST DATA ONLY")

    def test_runtime_rejects_repeated_canonical_source(self):
        alias = self.root / "sub" / ".." / "legacy.xls"
        self.assert_rejected_before_host(
            {"inputs": [str(self.source), str(alias)]}, "重复来源"
        )

    def test_runtime_rejects_selected_single_symlink(self):
        link = self.root / "alias.xls"
        link.symlink_to(self.source)
        self.assert_rejected_before_host({"input": str(link)}, "符号链接")

    def test_runtime_rejects_selected_batch_symlink(self):
        link = self.root / "alias.xls"
        link.symlink_to(self.source)
        self.assert_rejected_before_host({"inputs": [str(link)]}, "符号链接")


if __name__=='__main__':
    unittest.main()
