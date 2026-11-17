import datetime,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from pim_tool import validate,call,_filter_note_response
from worker import Worker

class PIMTests(unittest.TestCase):
 def test_validation(self):
  with self.assertRaises(ValueError):validate('calendar','create',{'title':'x','start':'2026-11-09T10:00','end':'2026-11-09T11:00'})
  with self.assertRaises(ValueError):validate('notes','update',{'id':'x','body':'new'})
  with self.assertRaises(ValueError):validate('notes','delete',{'id':'x','expected_modified':1,'shell':'rm'})
  validate('reminders','update',{'id':'x','expected_modified':1,'due':None,'completed':True})
 def test_current_task_evidence_and_no_background_writes(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);ctx=dict(job_id='j',kind='ask',question='请添加测试提醒。给我加个测试提醒。',created=time.time());(root/'pim-task.json').write_text(json.dumps(ctx))
   with self.assertRaises(ValueError):call('reminders','create',{'title':'x'},evidence='伪造请求',workspace=root)
   ctx['kind']='briefing';(root/'pim-task.json').write_text(json.dumps(ctx))
   with self.assertRaises(ValueError):call('reminders','create',{'title':'x'},evidence='请添加测试提醒',workspace=root)
 def test_idempotent_write_returns_existing_receipt(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'pim-task.json').write_text(json.dumps(dict(job_id='j',kind='ask',question='请添加测试提醒。给我加个测试提醒。',created=time.time())))
   first=call('reminders','create',{'title':'x'},evidence='请添加测试提醒',workspace=root,timeout=0)
   req=next((root/'pim-requests').glob('*.request.json'));v=json.loads(req.read_text());folder=root/'pim-requests/responses';folder.mkdir();(folder/(v['request_id']+'.json')).write_text(json.dumps(dict(ok=True,request_id=v['request_id'],data={'id':'native-id'})))
   second=call('reminders','create',{'title':'x'},evidence='给我加个测试提醒',workspace=root,timeout=1)
   self.assertEqual(first['request_id'],second['request_id']);self.assertEqual(second['data']['id'],'native-id');self.assertEqual(len(list((root/'pim-requests').glob('*.request.json'))),1)
 def test_worker_exposes_tool_context_without_note_contents(self):
  with tempfile.TemporaryDirectory() as d:
   w=Worker(dict(state_dir=d,codex='/fake',pim_tools_enabled=True))
   task=dict(job=dict(id='j',kind='ask',question='添加日程'),profile={},history=[],memories=[])
   prompt=w.prompt(task);self.assertIn('pim_tool.py',prompt);self.assertTrue((w.workspace/'pim-task.json').exists());self.assertEqual(json.loads((w.workspace/'pim-task.json').read_text())['question'],'添加日程')
   cmd=w.command('ask',Path(d)/'out');self.assertEqual(cmd[cmd.index('--sandbox')+1],'workspace-write')
class NotesRecoveryTests(unittest.TestCase):
 def test_global_list_hides_recovery_items_and_get_marks_them(self):
  with patch('pim_tool._trash_note_ids',return_value={'trashed'}):
   value=_filter_note_response(dict(ok=True,data=dict(notes=[{'id':'trashed'},{'id':'active'}],truncated=False)),'list',{},Path('/test'),1)
   self.assertEqual(value['data']['notes'],[{'id':'active'}]);self.assertEqual(value['data']['excluded_recently_deleted'],1)
   value=_filter_note_response(dict(ok=True,data=dict(id='trashed',title='x')),'get',{},Path('/test'),1)
   self.assertTrue(value['data']['in_recently_deleted'])
 def test_second_delete_is_rejected_before_any_native_write(self):
  with tempfile.TemporaryDirectory() as d,patch('pim_tool.call',return_value=dict(ok=True,data={'id':'trashed','in_recently_deleted':True})):
   root=Path(d);(root/'pim-task.json').write_text(json.dumps(dict(job_id='j',kind='ask',question='删除测试笔记',created=time.time())))
   with self.assertRaises(ValueError):call('notes','delete',{'id':'trashed','expected_modified':1},evidence='删除测试笔记',workspace=root,timeout=1)
   self.assertEqual(list((root/'pim-requests').glob('*.request.json')),[])

if __name__=='__main__':unittest.main()
