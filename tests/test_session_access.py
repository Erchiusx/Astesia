import fcntl,json,os,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from worker import Worker,writer_conflict_before_turn

class SessionAccessTests(unittest.TestCase):
 def test_only_initialization_conflicts_can_be_retried(self):
  error='thread-store conflict: thread x already has an active writer'
  self.assertTrue(writer_conflict_before_turn(error,''))
  self.assertFalse(writer_conflict_before_turn(error,json.dumps(dict(type='turn.started'))))
  self.assertFalse(writer_conflict_before_turn(error,json.dumps(dict(type='item.completed'))))
  self.assertFalse(writer_conflict_before_turn('other failure',''))
 def test_busy_initialization_is_retried_without_replaying_executed_work(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);w=Worker(dict(state_dir=temporary,codex=sys.executable,speech_tool_enabled=True));run=root/'run';run.mkdir()
   script=root/'fake.py';counter=root/'counter'
   script.write_text("from pathlib import Path\nimport sys\np=Path(sys.argv[1]);n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1))\nif n==0:\n print('thread-store conflict: already has an active writer',file=sys.stderr);sys.exit(1)\nprint('{\\\"type\\\":\\\"turn.started\\\"}')\n")
   result=w.execute_model([sys.executable,str(script),str(counter)],'synthetic',run,dict(id='test',kind='ask',expires=time.time()+15))
   self.assertEqual(result,0);self.assertEqual(counter.read_text(),'2')
 def test_worker_waits_for_terminal_lock_and_continues_after_release(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);w=Worker(dict(state_dir=temporary,codex=sys.executable,speech_tool_enabled=True));run=root/'run';run.mkdir()
   held=(root/'session-access.lock').open('a');fcntl.flock(held,fcntl.LOCK_EX)
   result=[]
   thread=threading.Thread(target=lambda:result.append(w.execute_model([sys.executable,'-c','print(123)'],'synthetic',run,dict(id='test',kind='ask',expires=time.time()+15))))
   thread.start();time.sleep(.4);self.assertEqual(result,[]);self.assertEqual(w.state,'waiting_for_session')
   held.close();thread.join(5);self.assertEqual(result,[0])

if __name__=='__main__':unittest.main()
