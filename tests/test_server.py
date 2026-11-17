import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import io
import datetime as dt
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from server import Store, Handler


RESULT = dict(reply='完整回答', spoken_text='语音回答', should_notify=True,
              sources=[], memory_updates=[])


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'state.db', 'client-secret', 'worker-secret')
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.store = self.store
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.store.db.close()
        self.tmp.cleanup()

    def api(self, path, payload=None, token='client-secret'):
        request = Request(self.base+path, headers={'Authorization': 'Bearer '+token,
                        'Content-Type': 'application/json'},
                        data=None if payload is None else json.dumps(payload).encode())
        with urlopen(request) as response:
            data = response.read().decode()
            return response.status, json.loads(data) if 'application/json' in response.headers['Content-Type'] else data

    def finish(self, job, result=None):
        self.store.complete(job['id'], job['lease_token'], result or RESULT)

    def test_role_separation_and_missing_auth(self):
        for path, token, payload in [('/health','wrong',None),('/memories','wrong',None),('/memories','worker-secret',None),('/worker/claim','client-secret',{}),('/ask','worker-secret',{'text':'x'})]:
            with self.assertRaises(HTTPError) as err:
                self.api(path,payload,token)
            self.assertEqual(err.exception.code,401)

    def test_round_trip_and_history(self):
        code, first = self.api('/ask',{'text':'记住我偏好安静的散步路线'})
        self.assertEqual(code,202)
        _, task = self.api('/worker/claim',{},'worker-secret')
        job = task['task']['job']
        result = dict(RESULT, memory_updates=[dict(text='偏好安静的散步路线',evidence='记住我偏好安静的散步路线')])
        self.finish(job,result)
        _, response = self.api('/jobs/'+first['id'])
        self.assertTrue(response['done'])
        self.assertEqual(response['delivery_status'],'ready')
        self.assertEqual(response['spoken_text'],'语音回答')
        self.api('/ask',{'text':'那雨天呢'})
        task = self.store.claim()
        self.assertEqual(task['history'][0]['question'],'记住我偏好安静的散步路线')
        self.assertEqual(task['memories'],['偏好安静的散步路线'])

    def test_arbitrary_memory_is_not_saved(self):
        self.store.enqueue('现在天气怎样')
        task=self.store.claim()
        self.finish(task['job'],dict(RESULT,memory_updates=[dict(text='虚构偏好',evidence='记住我偏好晴天')]))
        self.assertEqual(self.store.memory.active(),[])

    def test_invalid_memory_does_not_discard_answer_and_memory_api_is_readable(self):
        self.store.enqueue('What is the weather?');job=self.store.claim()['job']
        result=dict(RESULT,memory_updates=[dict(operation='upsert',key='invented.fact',
            category='profile',text='Invented fact',evidence='Not in user question',
            evidence_source='user',evidence_key='',version=0,expires_at='',confidence=1,reason='test')])
        report=self.store.complete(job['id'],job['lease_token'],result)
        self.assertEqual(self.store.job(job['id'])['status'],'completed')
        self.assertTrue(report['memory_report']['rejected'])
        self.assertEqual(self.api('/memories')[1],{'memories':[]})

    def test_interactive_priority_and_dedup(self):
        briefing = self.store.enqueue('检查','briefing','briefing',dedup='hour:one')
        self.assertEqual(briefing['id'],self.store.enqueue('再检查','briefing','briefing',dedup='hour:one')['id'])
        ask=self.store.enqueue('question',dedup='my-request')
        self.assertEqual(ask['id'],self.store.enqueue('question',dedup='my-request')['id'])
        self.assertEqual(self.store.claim()['job']['id'],ask['id'])

    def test_lease_recovery_rejects_old_completion(self):
        self.store.enqueue('question')
        old=self.store.claim()['job']
        with self.store.db:
            self.store.db.execute('update jobs set lease_until=0 where id=?',(old['id'],))
        recovered=self.store.claim()['job']
        self.assertNotEqual(old['lease_token'],recovered['lease_token'])
        with self.assertRaises(ValueError): self.finish(old)
        self.finish(recovered)
        self.assertEqual(self.store.job(old['id'])['status'],'completed')

    def test_expired_questions_not_executed(self):
        old=self.store.enqueue('old')
        with self.store.db: self.store.db.execute('update jobs set expires=0 where id=?',(old['id'],))
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.job(old['id'])['status'],'expired')

    def test_briefing_suppression_delivery_and_freshness(self):
        self.store.enqueue('check','briefing','briefing')
        job=self.store.claim()['job']
        self.finish(job,dict(RESULT,should_notify=False))
        self.assertEqual(self.store.briefing(True),'')
        self.assertIn('语音回答',self.store.briefing(False))
        with self.store.db: self.store.db.execute('update jobs set result=? where id=?',(json.dumps(RESULT),job['id']))
        self.assertIn('语音回答',self.store.briefing(True))
        self.assertEqual(self.store.briefing(True),'')
        with self.store.db: self.store.db.execute('update jobs set finished=0 where id=?',(job['id'],))
        self.assertEqual(self.store.briefing(True),'')
        self.assertNotIn('语音回答',self.store.briefing(False))

    def test_offline_status_does_not_discard_question(self):
        _,job=self.api('/ask',dict(text='hello'))
        self.assertFalse(job['worker_online'])
        _,text=self.api('/answer')
        self.assertIn('离线',text)
        self.store.heartbeat(dict(name='test',state='idle'))
        self.assertTrue(self.store.online())
        self.assertEqual(self.store.claim()['job']['id'],job['id'])

    def test_weather_trigger_ignores_baseline_and_detects_storm(self):
        self.store.set_meta('singapore_weather_enabled',True)
        end=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=2)).isoformat()
        def data(issued,forecast):
            return io.BytesIO(json.dumps({'data':{'items':[{'update_timestamp':issued,'valid_period':{'end':end},'forecasts':[{'forecast':forecast}]}]}}).encode())
        with patch('server.urlopen',return_value=data('one','Cloudy')):
            self.store.weather_tick()
        self.assertIsNone(self.store.latest('briefing','briefing'))
        self.store.set_meta('weather_checked',0)
        with patch('server.urlopen',return_value=data('two','Thundery Showers')):
            self.store.weather_tick()
        job=self.store.latest('briefing','briefing')
        self.assertEqual(job['dedup'],'weather:two')

    def test_phone_plain_poll_and_new_conversation(self):
        _,job=self.api('/ask',{'text':'hello'})
        _,answer=self.api('/jobs/'+job['id']+'/speech')
        self.assertIn('离线',answer)
        self.store.heartbeat({})
        _,pending=self.api('/jobs/'+job['id'])
        self.assertEqual(pending['delivery_status'],'waiting')
        self.assertFalse(pending['done'])
        _,answer=self.api('/jobs/'+job['id']+'/speech')
        self.assertEqual(answer,'')
        claimed=self.store.claim()['job'];self.finish(claimed)
        _,answer=self.api('/jobs/'+job['id']+'/speech')
        self.assertIn('语音回答',answer)
        self.api('/conversation/new',{})
        _,answer=self.api('/answer')
        self.assertIn('没有提问记录',answer)

    def test_briefing_never_outlives_forecast(self):
        self.store.enqueue('check','briefing','briefing')
        job=self.store.claim()['job']
        past=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=2)).isoformat()
        self.finish(job,dict(RESULT,valid_until=past))
        self.assertEqual(self.store.briefing(True),'')
        self.assertNotIn('语音回答',self.store.briefing(False))

    def test_message_ack_persistence_and_progress_supersession(self):
        self.store.enqueue('question')
        job=self.store.claim()['job']
        self.store.progress(job['id'],job['lease_token'],'先查询日历中的时间冲突。')
        first=self.store.message()
        self.store.progress(job['id'],job['lease_token'],'重复的进度不会重复入队。')
        self.assertEqual(first['id'],self.store.message()['id'])
        self.finish(job)
        final=self.store.message()
        self.assertEqual(final['kind'],'final')
        self.assertEqual(final['spoken_text'],RESULT['spoken_text'])
        self.assertIsNone(self.store.message(first['id']))
        self.store.db.close()
        self.store=Store(Path(self.tmp.name)/'state.db','client-secret','worker-secret')
        self.http.store=self.store
        self.assertEqual(self.store.message()['id'],final['id'])
        self.api('/messages/'+final['id']+'/ack',{})
        self.api('/messages/'+final['id']+'/ack',{})
        self.assertIsNone(self.store.message())

    def test_messages_require_client_auth_and_reject_stale_progress(self):
        self.store.enqueue('question')
        job=self.store.claim()['job']
        with self.assertRaises(ValueError): self.store.progress(job['id'],'wrong','hello')
        with self.assertRaises(HTTPError): self.api('/messages/next',token='worker-secret')
        self.finish(job)
        with self.assertRaises(ValueError): self.store.progress(job['id'],job['lease_token'],'late')
        _,data=self.api('/messages/next')
        self.assertEqual(data['message']['job_id'],job['id'])
        self.assertNotIn('wake_error',data['message'])

    def test_silent_or_stale_briefings_do_not_become_playable_messages(self):
        for result in [dict(RESULT,should_notify=False),dict(RESULT,valid_until='2000-01-01T00:00:00+08:00')]:
            self.store.enqueue('check','briefing','briefing')
            self.finish(self.store.claim()['job'],result)
        self.assertIsNone(self.store.message())

    def test_expiration_without_worker_generates_one_failure_message(self):
        old=self.store.enqueue('old')
        with self.store.db: self.store.db.execute('update jobs set expires=0 where id=?',(old['id'],))
        self.store.expire_jobs();self.store.expire_jobs()
        self.assertEqual(self.store.message()['job_id'],old['id'])
        self.assertEqual(self.store.db.execute('select count(*) from messages').fetchone()[0],1)

    def test_audio_activation_skips_backlog_and_claim_is_exclusive(self):
        self.store.enqueue('old');self.finish(self.store.claim()['job'])
        self.assertIsNone(self.store.claim_audio())
        self.store.enable_audio()
        self.assertIsNone(self.store.claim_audio())
        self.store.enqueue('new');self.finish(self.store.claim()['job'])
        message=self.store.claim_audio()
        self.assertEqual(message['spoken_text'],RESULT['spoken_text'])
        self.assertIsNone(self.store.claim_audio())
        with self.assertRaises(ValueError):self.store.complete_audio(message['id'],'wrong')
        self.store.complete_audio(message['id'],message['lease_token'])
        self.assertIsNone(self.store.claim_audio())
        self.assertIsNotNone(self.store.db.execute('select acknowledged from messages where id=?',(message['id'],)).fetchone()[0])
        self.assertEqual(self.store.db.execute("select audio_status from messages where id=?",(message['id'],)).fetchone()[0],'sent')

    def test_audio_retry_expired_lease_and_superseded_progress(self):
        self.store.enable_audio()
        self.store.enqueue('hello');job=self.store.claim()['job']
        self.store.progress(job['id'],job['lease_token'],'查询具体日程。')
        progress=self.store.claim_audio()
        self.store.complete_audio(progress['id'],progress['lease_token'],'connection error')
        self.assertIsNone(self.store.claim_audio())
        self.finish(job)
        message=self.store.claim_audio()
        self.assertEqual(message['kind'],'final')
        with self.store.db:self.store.db.execute('update messages set audio_until=0 where id=?',(message['id'],))
        new=self.store.claim_audio()
        self.assertNotEqual(new['lease_token'],message['lease_token'])
        with self.assertRaises(ValueError):self.store.complete_audio(message['id'],message['lease_token'])
        self.store.complete_audio(new['id'],new['lease_token'])
        self.assertIsNone(self.store.claim_audio())

    def test_audio_worker_api_role_separation(self):
        with self.assertRaises(HTTPError): self.api('/worker/messages/claim',{})
        with self.assertRaises(HTTPError): self.api('/worker/messages/heartbeat',{})
        with self.assertRaises(HTTPError): self.api('/delivery/zello/enable',{},token='worker-secret')
        self.api('/delivery/zello/enable',{})
        self.assertEqual(self.api('/worker/messages/claim',{},token='worker-secret')[1],{'message':None})

    def test_audio_heartbeat_extends_live_lease_but_fences_stale_sender(self):
        self.store.enable_audio()
        self.store.enqueue('hello');self.finish(self.store.claim()['job'])
        message=self.store.claim_audio();payload=dict(message_id=message['id'],lease_token=message['lease_token'])
        with self.store.db:self.store.db.execute('update messages set audio_until=? where id=?',(time.time()+5,message['id']))
        self.assertEqual(self.api('/worker/messages/heartbeat',payload,token='worker-secret')[0],200)
        self.assertGreater(self.store.db.execute('select audio_until from messages where id=?',(message['id'],)).fetchone()[0],time.time()+200)
        with self.assertRaises(ValueError):self.store.renew_audio(message['id'],'wrong')
        with self.store.db:self.store.db.execute('update messages set audio_until=0 where id=?',(message['id'],))
        with self.assertRaises(ValueError):self.store.renew_audio(message['id'],message['lease_token'])
        replacement=self.store.claim_audio()
        with self.assertRaises(ValueError):self.store.renew_audio(message['id'],message['lease_token'])
        self.store.renew_audio(replacement['id'],replacement['lease_token'])
        self.store.complete_audio(replacement['id'],replacement['lease_token'])
        with self.assertRaises(ValueError):self.store.renew_audio(replacement['id'],replacement['lease_token'])


if __name__=='__main__':
    unittest.main()
