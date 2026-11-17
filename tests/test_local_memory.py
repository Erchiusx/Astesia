import contextlib,io,json,sqlite3,subprocess,tempfile,time,unittest
from pathlib import Path
from local_memory import LocalMemory
from test_memory import change
from server import Store
from worker import Worker

class LocalMemoryTests(unittest.TestCase):
 def test_seed_keeps_versions_evidence_and_audit(self):
  with tempfile.TemporaryDirectory() as d:
   cloud=Store(Path(d)/'cloud.db','c','w');q='I like quiet routes.'
   with cloud.db:cloud.memory.apply([change('Quiet routes',q)],q,'j','ask')
   export=dict(items=[dict(x) for x in cloud.db.execute('select * from memory_items')],audit=[dict(x) for x in cloud.db.execute('select * from memory_audit')])
   local=LocalMemory(Path(d)/'local');local.seed(export)
   self.assertEqual(local.active(),cloud.memory.active());self.assertEqual(local.read(['preference.walking_routes'])[0]['evidence'],q)
   self.assertEqual(local.db.execute('select count(*) from memory_audit').fetchone()[0],1)
   with self.assertRaises(ValueError):local.seed(export)
   local.close();cloud.db.close()
 def test_local_update_retry_and_delete(self):
  with tempfile.TemporaryDirectory() as d:
   m=LocalMemory(Path(d));q='I like quiet routes.';ctx=dict(job_id='j',kind='ask',question=q)
   item=change('Quiet routes',q);r=m.apply([item],ctx);self.assertEqual(r['accepted'],['preference.walking_routes'])
   self.assertEqual(m.apply([item],ctx),r);self.assertEqual(m.active()[0]['version'],1)
   q='Forget the route preference.';ctx=dict(job_id='k',kind='ask',question=q)
   m.apply([change('',q,operation='delete',version=1)],ctx)
   self.assertEqual(m.index(),[]);self.assertEqual(m.read(['preference.walking_routes'])[0]['version'],2)
   self.assertEqual(m.db.execute('select count(*) from memory_audit').fetchone()[0],2)
   self.assertTrue(list((Path(d)/'backups').glob('*.sqlite3')));m.close()
 def test_prompt_selective_and_helper_reads_local_offline(self):
  with tempfile.TemporaryDirectory() as d:
   w=Worker(dict(state_dir=d,memory_authority='mac',codex='/bin/false'))
   q='Sentinel private fact';w.memory.apply([change(q,q)],dict(job_id='seed',kind='ask',question=q))
   task=dict(job=dict(id='j',kind='ask',question='Hello'),profile={},history=[],memories=['STALE_CLOUD'],memory_records=[dict(text='STALE_CLOUD')])
   prompt=w.prompt(task)
   self.assertNotIn(q,prompt);self.assertNotIn('STALE_CLOUD',prompt);self.assertIn('preference.walking_routes',prompt)
   self.assertEqual(w.command('ask',Path(d)/'r')[w.command('ask',Path(d)/'r').index('--sandbox')+1],'workspace-write')
   p=subprocess.run(['python3','memory_tool.py','read','preference.walking_routes'],cwd=w.workspace,capture_output=True,text=True)
   self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads(p.stdout)[0]['text'],q);w.memory.close()
 def test_cloud_archive_stays_frozen(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'db','c','w');q='Quiet routes.'
   with s.db:s.memory.apply([change(q,q)],q,'seed','ask')
   s.set_meta('memory_authority','mac');job=s.enqueue('Hello');task=s.claim()
   self.assertEqual(task['memories'],[]);self.assertEqual(task['memory_records'],[])
   result=dict(reply='Hello',spoken_text='Hello',should_notify=True,memory_updates=[change('Bad cloud edit','Hello',key='preference.bad')])
   receipt=s.complete(task['job']['id'],task['job']['lease_token'],result,None)
   self.assertEqual(receipt['memory_authority'],'mac');self.assertEqual(len(s.memory.active()),1);s.db.close()

if __name__=='__main__':unittest.main()
