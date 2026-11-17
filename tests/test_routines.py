import datetime as dt,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from server import Store
from routine_store import validate_health
from wellness_data import WellnessSnapshot,records,summarize
from voice_core import VoiceDelivery,VoiceExpired

class RoutineTests(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'db','c','w')
 def tearDown(self):self.s.db.close();self.temp.cleanup()
 def test_wake_is_delayed_and_deduplicated(self):
  now=dt.datetime(2026,11,7,12,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
  with patch('routine_store.time.time',return_value=now):v=self.s.routine.signal({'event':'wake'})
  self.assertTrue(v['scheduled'])
  with patch('routine_store.time.time',return_value=now+1799):self.s.routine.tick()
  self.assertEqual(self.s.db.execute('select count(*) from jobs').fetchone()[0],0)
  with patch('routine_store.time.time',return_value=now+1801):
   self.s.routine.tick();self.s.routine.tick();self.assertTrue(self.s.routine.signal({'event':'wake'})['duplicate'])
  row=self.s.db.execute('select * from jobs').fetchone();self.assertEqual(row['input_source'],'routine');self.assertEqual(json.loads(row['input_meta'])['routine_phase'],'morning')
  self.assertEqual(self.s.db.execute('select count(*) from jobs').fetchone()[0],1)
 def test_evening_offset_not_guessed_and_crosses_midnight(self):
  self.assertFalse(self.s.routine.signal({'event':'wind_down'})['scheduled'])
  self.s.set_meta('routine_wind_down_minutes',30)
  now=dt.datetime(2026,11,7,23,50,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
  from zoneinfo import ZoneInfo
  with patch('routine_store.SG',ZoneInfo('Asia/Singapore')),patch('routine_store.time.time',return_value=now):v=self.s.routine.signal({'event':'wind_down'})
  self.assertEqual(v['due'],'2026-11-08T00:15:00+08:00');self.assertEqual(self.s.db.execute('select id from routine_events').fetchone()[0],'evening:2026-11-08')
 def test_expired_events_not_replayed_after_outage(self):
  now=time.time();self.s.routine.signal({'event':'wake'})
  with patch('routine_store.time.time',return_value=now+3600):self.s.routine.tick()
  self.assertEqual(self.s.db.execute('select count(*) from jobs').fetchone()[0],0);self.assertEqual(self.s.db.execute('select status from routine_events').fetchone()[0],'expired')
 def test_routine_does_not_merge_into_hourly_and_is_prioritized(self):
  now=time.time();self.s.enqueue('hourly','briefing','briefing','hour-test');self.s.routine.signal({'event':'wake'})
  with patch('routine_store.time.time',return_value=now+1801):
   self.s.routine.tick();task=self.s.claim()
  self.assertEqual(task['job']['input_source'],'routine')
  self.assertEqual(self.s.db.execute('select count(*) from jobs').fetchone()[0],2)
 def test_completed_evening_delivery_cannot_cross_bedtime(self):
  now=time.time();self.s.set_meta('routine_wind_down_minutes',15);self.s.enable_audio();self.s.routine.signal({'event':'wind_down'})
  with patch('routine_store.time.time',return_value=now+601):
   self.s.routine.tick();task=self.s.claim();j=task['job'];self.s.complete(j['id'],j['lease_token'],dict(reply='晚安简报',spoken_text='晚安简报',should_notify=True,memory_updates=[]),None)
  message=self.s.db.execute('select * from messages').fetchone();self.assertLessEqual(message['expires'],now+901)
 def test_preview_never_schedules(self):
  self.assertTrue(self.s.routine.signal({'event':''})['preview']);self.assertEqual(self.s.db.execute('select count(*) from routine_events').fetchone()[0],0)

class HealthRelayTests(unittest.TestCase):
 def test_scope_validation_and_ack(self):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'db','c','w');v=dict(schema_version='1',captured_at=dt.datetime.now(dt.timezone.utc).isoformat(),heart_rate_values='70')
   with self.assertRaises(ValueError):validate_health(dict(v,medical_records='not_allowed'))
   item=s.routine.ingest(v);snapshot=s.routine.pull()['snapshot'];self.assertEqual(snapshot['id'],item['id']);s.routine.ack(item['id']);self.assertIsNone(s.routine.pull()['snapshot']);s.db.close()
 def test_parallel_values_checked_and_sources_not_combined(self):
  now=dt.datetime.now(dt.timezone.utc).isoformat();p=dict(steps_values='10\n20',steps_starts=now+'\n'+now,steps_ends=now+'\n'+now,steps_sources='Watch\nPhone',steps_units='count')
  v=summarize(p)['steps'];self.assertEqual(len(v['today_observed_by_source']),2);self.assertFalse(v['today_observed_by_source'][0]['is_health_app_daily_total'])
  p['steps_sources']='a\nb\nc';self.assertEqual(records(p,'steps')['status'],'unparseable')
 def test_sleep_overlap_union_and_unknown_labels(self):
  p=dict(sleep_values='Core\nDeep\nUnknown',sleep_starts='2026-11-07T00:00:00+08:00\n2026-11-07T01:00:00+08:00\n2026-11-07T02:00:00+08:00',sleep_ends='2026-11-07T02:00:00+08:00\n2026-11-07T03:00:00+08:00\n2026-11-07T03:30:00+08:00',sleep_sources='Watch')
  v=summarize(p)['sleep']['by_source'][0];self.assertEqual(v['recognized_asleep_hours'],3);self.assertEqual(v['unclassified_samples'],1)
 def test_private_local_snapshot_freshness(self):
  with tempfile.TemporaryDirectory() as d:
   w=WellnessSnapshot(d);self.assertEqual(w.load()['status'],'not_connected')
   w.install(dict(id='test',payload=dict(captured_at=dt.datetime.now(dt.timezone.utc).isoformat())))
   self.assertEqual(w.load()['status'],'available');self.assertEqual((Path(d)/'latest.json').stat().st_mode&0o777,0o600)
   v=json.loads((Path(d)/'latest.json').read_text());v['payload']['captured_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=2)).isoformat();(Path(d)/'latest.json').write_text(json.dumps(v));self.assertEqual(w.load()['status'],'stale')
 def test_expired_voice_never_starts_synthesis(self):
  v=VoiceDelivery({})
  with self.assertRaises(VoiceExpired):v.process(dict(expires=time.time()-1))

if __name__=='__main__':unittest.main()

class RoutineAPITests(unittest.TestCase):
 def setUp(self):
  import test_server
  self.helper=test_server.BrokerTests();self.helper.setUp()
 def tearDown(self):self.helper.tearDown()
 def test_role_separation_preview_and_health_transfer(self):
  from urllib.error import HTTPError
  api=self.helper.api
  _,status=api('/routine/status');self.assertIsNone(status['config']['wind_down_minutes'])
  _,preview=api('/routine/signal',{'event':''});self.assertTrue(preview['preview'])
  payload=dict(schema_version='1',captured_at=dt.datetime.now(dt.timezone.utc).isoformat())
  with self.assertRaises(HTTPError):api('/wellness/ingest',payload,'worker-secret')
  with self.assertRaises(HTTPError):api('/worker/wellness/pull',{},'client-secret')
  _,accepted=api('/wellness/ingest',payload);self.assertTrue(accepted['queued'])
  _,received=api('/worker/wellness/pull',{},'worker-secret');self.assertEqual(received['snapshot']['id'],accepted['id'])
  api('/worker/wellness/ack',{'id':accepted['id']},'worker-secret');_,empty=api('/worker/wellness/pull',{},'worker-secret');self.assertIsNone(empty['snapshot'])

class DailyPlanTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'db','c','w')
  self.s.set_meta('routine_daily_plan',dict(enabled=True,wake='09:00',bedtime='23:40',timezone='Asia/Singapore'))
 def tearDown(self):self.s.db.close();self.temp.cleanup()
 def at(self,hour,minute):return dt.datetime(2026,11,7,hour,minute,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
 def test_explicit_plan_seeds_correct_times_once_and_dispatches(self):
  with patch('routine_store.time.time',return_value=self.at(9,0)):
   self.s.routine.tick();self.s.routine.tick()
  rows=self.s.db.execute('select * from routine_events order by due').fetchall()
  self.assertEqual(len(rows),4)
  self.assertEqual(rows[0]['due'],self.at(9,30));self.assertEqual(rows[1]['due'],self.at(23,35));self.assertEqual(rows[1]['deadline'],self.at(23,40))
  with patch('routine_store.time.time',return_value=self.at(9,30)+1):
   self.s.routine.tick();task=self.s.claim();self.assertEqual(task['job']['input_source'],'routine')
 def test_enabling_after_bedtime_does_not_replay_tonight(self):
  with patch('routine_store.time.time',return_value=self.at(23,41)):self.s.routine.tick()
  rows=self.s.db.execute('select * from routine_events').fetchall();self.assertEqual(len(rows),2)
  self.assertTrue(all('2026-11-08' in r['id'] for r in rows));self.assertEqual(self.s.db.execute('select count(*) from jobs').fetchone()[0],0)
 def test_late_restart_expires_existing_reminder_without_replay(self):
  with patch('routine_store.time.time',return_value=self.at(23,30)):self.s.routine.tick()
  with patch('routine_store.time.time',return_value=self.at(23,41)):self.s.routine.tick()
  self.assertEqual(self.s.db.execute("select status from routine_events where id='evening:2026-11-07'").fetchone()[0],'expired')
  self.assertEqual(self.s.db.execute('select count(*) from jobs').fetchone()[0],0)
