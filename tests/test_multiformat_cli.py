"""New CLI commands exercise their real Host using isolated local data."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class MultiformatCliTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for plugin in ('data-preview','data-check','data-compare','schema-diff'):
            shutil.copytree(ROOT/'plugins'/plugin,self.root/'plugins'/plugin,ignore=shutil.ignore_patterns('__pycache__'))
        self.env={key:value for key,value in os.environ.items() if not key.startswith('TESTBOX_')}
        self.env.update({'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1','HOME':str(self.root/'home'),
                         'XDG_DATA_HOME':str(self.root/'home/data'),'LOCALAPPDATA':str(self.root/'home/local')})
        (self.root/'home').mkdir()

    def call(self, command, params):
        param_file=self.root/'params.json'
        param_file.write_text(json.dumps(params,ensure_ascii=False),encoding='utf-8')
        process=subprocess.run([sys.executable,'-m','testbox.cli','--json','run',command,'--params-file',str(param_file)],
                               cwd=self.root,env=self.env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertEqual(process.stderr.strip(),'')
        result=json.loads(process.stdout)
        self.assertEqual(result['status'],'success',result)
        self.assertTrue(result['files'])
        return result

    def test_cross_format_preview_compare_and_check(self):
        txt=self.root/'a.txt'; data=self.root/'b.json'
        txt.write_text('id||amount<EOR>001||12.00<EOR>',encoding='utf-8')
        data.write_text('[{"id":"001","amount":"12"}]',encoding='utf-8')
        opts={'format':'txt','delimiter':'||','record_separator':'<EOR>'}
        norm={'types':{'amount':'decimal'}}
        preview=self.call('data.preview',{'input':str(txt),'options':opts,'normalize':norm})
        self.assertEqual(preview['data']['rows'],[{'id':'001','amount':'12'}])
        compared=self.call('data.compare',{'left':str(txt),'right':str(data),'left_options':opts,
            'left_normalize':norm,'right_normalize':norm,'mode':'key','keys':['id']})
        self.assertTrue(compared['data']['equal'])
        checked=self.call('data.check',{'input':str(txt),'options':opts,'rules':[{'type':'required','fields':['id']},{'type':'unique','fields':['id']}]})
        self.assertTrue(checked['data']['passed'])

    def test_sql_preview_unknown_not_equal_and_complete_ddl_equal(self):
        sql='CREATE TABLE t (id INTEGER, name VARCHAR(10));'
        preview=self.call('sql.preview',{'text':sql})
        self.assertEqual(preview['data']['total_rows'],2)
        equal=self.call('sql.diff',{'left_text':sql,'right_text':sql})
        self.assertEqual(equal['data']['verdict'],'equal')
        unknown=self.call('sql.diff',{'left_text':sql,'right_text':'SELECT * FROM t;'})
        self.assertEqual(unknown['data']['verdict'],'inconclusive')
        self.assertFalse(unknown['data']['complete'])


if __name__=='__main__':
    unittest.main()
