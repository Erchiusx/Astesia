import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from server import Store
from worker import Worker
from speech_mcp import SpeechTool,TOOL

class SpeechQueueTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'state.db','c','w');self.s.set_meta('memory_authority','mac');self.s.enable_audio()
  self.s.enqueue('synthetic question');self.job=self.s.claim()['job']
 def tearDown(self):self.s.db.close();self.tmp.cleanup()
 def speak(self,text='First sentence',key='11111111-1111-4111-8111-111111111111'):
  return self.s.speak(self.job['id'],self.job['lease_token'],text,key)
 def test_speech_is_queued_while_job_running_and_final_does_not_duplicate(self):
  receipt=self.speak();self.assertTrue(receipt['queued'])
  audio=self.s.claim_audio();self.assertEqual(audio['spoken_text'],'First sentence');self.assertEqual(self.s.job(self.job['id'])['status'],'running')
  self.s.complete_audio(audio['id'],audio['lease_token'])
  self.speak('Finished work','22222222-2222-4222-8222-222222222222')
  self.s.complete(self.job['id'],self.job['lease_token'],dict(reply='Plain final text',delivery_mode='tool'))
  rows=self.s.db.execute('select kind,spoken_text from messages order by created').fetchall();self.assertEqual([r['kind'] for r in rows],['speech','speech'])
  next_audio=self.s.claim_audio();self.assertEqual(next_audio['spoken_text'],'Finished work')
  self.assertEqual(self.s.job(self.job['id'])['status'],'completed')
 def test_same_request_retries_are_idempotent_but_repetition_is_allowed(self):
  first=self.speak();self.assertEqual(self.speak(),first)
  with self.assertRaises(ValueError):self.speak('Different text')
  self.speak(key='22222222-2222-4222-8222-222222222222');self.assertEqual(self.s.db.execute('select count(*) from messages').fetchone()[0],2)
 def test_rejects_stale_scope_and_empty_text(self):
  with self.assertRaises(ValueError):self.speak(' ')
  with self.assertRaises(ValueError):self.s.speak(self.job['id'],'wrong','text','11111111-1111-4111-8111-111111111111')
  self.s.complete(self.job['id'],self.job['lease_token'],dict(reply='',delivery_mode='tool'))
  with self.assertRaises(ValueError):self.speak()
  self.assertIsNone(self.s.claim_audio())
 def test_routine_speech_keeps_cutoff_without_model_metadata(self):
  due=time.time()+60
  with self.s.db:self.s.db.execute("update jobs set input_source='routine',input_meta=? where id=?",(json.dumps(dict(delivery_deadline=due)),self.job['id']))
  self.speak();row=self.s.db.execute('select expires from messages').fetchone();self.assertEqual(row[0],due)

class SpeechProtocolTests(unittest.TestCase):
 def setUp(self):
  import test_server
  self.helper=test_server.BrokerTests();self.helper.setUp();self.root=Path(self.helper.tmp.name)
  self.helper.store.enqueue('synthetic');self.job=self.helper.store.claim()['job']
  (self.root/'config.json').write_text(json.dumps(dict(base_url=self.helper.base,worker_token='worker-secret')))
  self.path=self.root/'context.json';self.path.write_text(json.dumps(dict(job_id=self.job['id'],lease_token=self.job['lease_token'],expires=time.time()+300,config_path=str(self.root/'config.json'))))
  self.tool=SpeechTool(self.path)
 def tearDown(self):self.helper.tearDown()
 def test_only_text_is_public_and_call_returns_without_audio_playback(self):
  self.assertEqual(set(TOOL['inputSchema']['properties']),{'text'})
  response=self.tool.dispatch(dict(id=1,method='tools/call',params=dict(name='speak',arguments=dict(text='A queued sentence'))))
  self.assertFalse(response['result'].get('isError',False));self.assertEqual(self.helper.store.job(self.job['id'])['status'],'running')
  self.assertEqual(self.helper.store.db.execute("select count(*) from messages where kind='speech'").fetchone()[0],1)
  self.assertEqual(self.tool.dispatch(dict(id=1,method='tools/call',params=dict(name='speak',arguments=dict(text='A queued sentence')))),response)
 def test_initialize_and_tool_errors_do_not_claim_success(self):
  init=self.tool.dispatch(dict(id=0,method='initialize',params={'protocolVersion':'2025-06-18'}));self.assertEqual(init['result']['protocolVersion'],'2025-06-18')
  bad=self.tool.dispatch(dict(id=1,method='tools/call',params=dict(name='speak',arguments=dict(text='x',should_notify=True))))
  self.assertTrue(bad['result']['isError'])
  self.assertEqual(self.helper.store.db.execute('select count(*) from messages').fetchone()[0],0)
 def test_worker_separates_instructions_from_user_input_and_has_no_output_schema(self):
  instructions=self.root/'instructions.txt';instructions.write_text('PERSONA_IN_DEVELOPER_ROLE')
  w=Worker(dict(state_dir=str(self.root/'worker'),codex='/fake',speech_tool_enabled=True,instructions=str(instructions)))
  task=dict(job=self.job,profile={},history=[dict(question='old question',result=json.dumps(dict(reply='old answer',spoken_text='duplicate',should_notify=True)))],memories=[])
  prompt=w.prompt(task);self.assertNotIn('PERSONA_IN_DEVELOPER_ROLE',prompt);self.assertNotIn('should_notify',prompt);self.assertNotIn('old answer',prompt)
  command=w.command('ask',self.root/'run/result.txt');self.assertNotIn('--output-schema',command)
  self.assertTrue(any('developer_instructions=' in v and 'PERSONA_IN_DEVELOPER_ROLE' in v for v in command))

if __name__=='__main__':unittest.main()

class MemoryToolTests(unittest.TestCase):
 def test_memory_is_queried_and_maintained_without_model_version_fields(self):
  from memory_mcp import call
  from local_memory import LocalMemory
  with tempfile.TemporaryDirectory() as temporary:
   cfg=dict(state_dir=temporary,memory_authority='mac');ctx=dict(job_id='task-1',question='I prefer quiet routes.',kind='ask',created=time.time(),expires=time.time()+300)
   args=dict(key='preference.routes',text='Prefers quiet routes',evidence='I prefer quiet routes.')
   r=call('memory_upsert',args,ctx,cfg);self.assertEqual(r['accepted'],['preference.routes'])
   self.assertEqual(call('memory_upsert',args,ctx,cfg),r)
   self.assertEqual(call('memory_read',{'key':'preference.routes'},ctx,cfg)[0]['version'],1)
   self.assertEqual(len(call('memory_search',{'query':'quiet'},ctx,cfg)),1)
   bad=call('memory_upsert',dict(args,text='Invented fact',evidence='not said'),dict(ctx,job_id='task-2'),cfg);self.assertFalse(bad['accepted'])
   with self.assertRaises(ValueError):call('memory_forget',dict(key='preference.routes',evidence='I prefer quiet routes.'),dict(ctx,kind='briefing'),cfg)
   forget=dict(ctx,job_id='task-3',question='Forget my route preference.')
   self.assertEqual(call('memory_forget',dict(key='preference.routes',evidence=forget['question']),forget,cfg)['accepted'],['preference.routes'])
   self.assertEqual(call('memory_search',{'query':'quiet'},forget,cfg),[])
 def test_worker_reuses_only_the_matching_phone_conversation(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);instructions=root/'instructions.txt';instructions.write_text('persona')
   w=Worker(dict(state_dir=temporary,codex='/fake',speech_tool_enabled=True,instructions=str(instructions),reuse_conversation_sessions=True,persist_interactive_sessions=True))
   (root/'conversation-sessions.json').write_text(json.dumps({'phone-main':{'thread_id':'11111111-1111-4111-8111-111111111111'}}))
   w.active_conversation='phone-main';self.assertIn('resume',w.command('ask',root/'run/result.txt'))
   w.active_conversation='phone-new';self.assertNotIn('resume',w.command('ask',root/'run/result.txt'))
   w.active_conversation='phone-main';self.assertNotIn('resume',w.command('briefing',root/'run/result.txt'))

class HistorySearchTests(unittest.TestCase):
 def test_history_is_optional_scoped_to_current_conversation_and_bounded(self):
  with tempfile.TemporaryDirectory() as temporary:
   s=Store(Path(temporary)/'state.db','c','w');s.set_meta('memory_authority','mac')
   try:
    for index in range(7):
     s.enqueue('old '+str(index),'phone-main');j=s.claim()['job'];s.complete(j['id'],j['lease_token'],dict(reply='answer '+str(index),delivery_mode='tool'))
    s.enqueue('other private question','phone-other');j=s.claim()['job'];s.complete(j['id'],j['lease_token'],dict(reply='foreign answer',delivery_mode='tool'))
    s.enqueue('current question','phone-main');j=s.claim()['job'];turns=s.history_search(j['id'],j['lease_token'],'')['turns']
    self.assertEqual(len(turns),5);self.assertTrue(all('foreign' not in x['assistant'] for x in turns))
    with self.assertRaises(ValueError):s.history_search(j['id'],'bad','')
   finally:s.db.close()

class SpeechPlaybackTests(unittest.TestCase):
 def test_explicit_speech_does_not_gain_an_automatic_greeting(self):
  import wave,voice_core
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);(root/'voice-work').mkdir();spoken=[]
   delivery=voice_core.VoiceDelivery(dict(voice_greeting='Automatic greeting. ',opusenc='/fake/opusenc',node='/fake/node'))
   def synthesize(text,source,wav):
    spoken.append(text)
    with wave.open(str(wav),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(48000);f.writeframes(b'\x00\x00'*48000)
   delivery.synthesize=synthesize;delivery.command=lambda *a:None
   values={'ROOT':root}
   if hasattr(voice_core,'DATA_DIR'):values['DATA_DIR']=root
   with patch.dict(voice_core.__dict__,values):delivery.process(dict(id='synthetic',kind='speech',spoken_text='Only these words.',expires=time.time()+120))
   self.assertEqual(spoken,['Only these words.'])
