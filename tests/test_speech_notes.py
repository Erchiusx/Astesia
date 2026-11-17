import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from speech_notes import Collector,snapshot
from server import Store

class SpeechNotesTests(unittest.TestCase):
 def test_only_latest_three_unique_messages_are_rendered_latest_first(self):
  entries=[dict(id=str(i),text='message '+str(i)+'\n<&>',created=100+i) for i in range(6)]
  value=snapshot(entries+[entries[-1]],3,'UTC')
  self.assertEqual([e['id'] for e in value['entries']],['3','4','5'])
  self.assertLess(value['body'].index('message 5'),value['body'].index('message 4'))
  self.assertNotIn('message 2',value['body']);self.assertIn('<&>',value['body'])
  self.assertEqual(snapshot(entries,3,'UTC')['version'],value['version'])
 def test_invalid_retention_and_payload_are_rejected(self):
  with self.assertRaises(ValueError):snapshot([],4)
  with self.assertRaises(ValueError):snapshot([dict(id='a',text='',created=100)])
 def test_broker_filters_routines_before_applying_three_message_limit(self):
  with tempfile.TemporaryDirectory() as temporary:
   store=Store(Path(temporary)/'db','c','w')
   try:
    for i,phase in enumerate([None,'morning',None,'evening',None,'morning',None]):
     job=store.enqueue('synthetic '+str(i),'phone-main' if phase is None else 'routine-'+phase,'ask' if phase is None else 'briefing',input_source='text' if phase is None else 'routine',input_metadata={} if phase is None else dict(routine_phase=phase))
     claimed=store.claim()['job'];store.speak(claimed['id'],claimed['lease_token'],'speech '+str(i),str(__import__('uuid').uuid4()))
     store.complete(claimed['id'],claimed['lease_token'],dict(reply='',delivery_mode='tool'))
    rows=store.recent_speech_notes()['entries'];self.assertEqual([r['text'] for r in rows],['speech 2','speech 4','speech 6'])
    self.assertEqual(store.recent_speech_notes(time.time()+1)['entries'],[])
   finally:store.db.close()
 def test_collector_leaves_last_good_state_when_broker_is_down(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);(root/'config.json').write_text(json.dumps(dict(enabled=True,keep=3,timezone='UTC',mirror_id='test')))
   desired=root/'desired.json';desired.write_text('previous good state')
   collector=Collector(dict(base_url='https://example.com',worker_token='synthetic'),root)
   with patch('speech_notes.urlopen',side_effect=OSError('offline')):
    with self.assertRaises(OSError):collector.tick()
   self.assertEqual(desired.read_text(),'previous good state')

class SpeechNotesAPITests(unittest.TestCase):
 def test_worker_role_only(self):
  import test_server
  from urllib.error import HTTPError
  helper=test_server.BrokerTests();helper.setUp()
  try:
   with self.assertRaises(HTTPError):helper.api('/worker/speech-notes/recent',{},'client-secret')
   _,result=helper.api('/worker/speech-notes/recent',{},'worker-secret');self.assertEqual(result['entries'],[])
  finally:helper.tearDown()

if __name__=='__main__':unittest.main()

class SpeechNotesDeploymentTests(unittest.TestCase):
 def test_enabled_mirror_renders_an_independent_service_without_starting_it(self):
  import sys,plistlib
  from unittest.mock import patch
  sys.path.insert(0,str(Path(__file__).resolve().parents[1]));import manage
  with tempfile.TemporaryDirectory() as temporary:
   home=Path(temporary)/'private';manage.init(home,'mac','UTC')
   folder=home/'data/speech-notes';folder.mkdir();(folder/'config.json').write_text(json.dumps(dict(enabled=True)))
   with patch.object(manage,'doctor',return_value=True):manage.install(home)
   service=plistlib.loads((home/'services/org.personalagent.oss.speech-notes.plist').read_bytes())
   args=service['ProgramArguments'];self.assertIn(str(home.resolve()/'config/worker-config.json'),args);self.assertIn(str(home.resolve()/'data/speech-notes'),args)
   self.assertTrue(service['KeepAlive'])
