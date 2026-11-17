"""Small HTTPS-behind-nginx broker. No model or credentials for Codex live here."""
import datetime as dt
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request
import uuid
from memory_store import MemoryStore
from routine_store import RoutineStore
from conversation_api import dispatch as conversation_dispatch

from runtime_paths import TIMEZONE as SG
PREFIX = '/personal-agent'


class Store:
    def __init__(self, path, client_token, worker_token):
        self.client_token, self.worker_token = client_token, worker_token
        self.lock = threading.RLock()
        self.queue_ready = threading.Condition(self.lock)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, kind TEXT, conversation TEXT, question TEXT,
              status TEXT, created REAL, started REAL, finished REAL, expires REAL,
              lease_until REAL, lease_token TEXT, attempts INTEGER DEFAULT 0,
              result TEXT, error TEXT, delivered INTEGER DEFAULT 0,
              dedup TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS memories (
              text TEXT PRIMARY KEY, evidence TEXT, created REAL);
            CREATE INDEX IF NOT EXISTS queue ON jobs(status, kind, created);
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, event_key TEXT UNIQUE, job_id TEXT, kind TEXT,
              reply TEXT, spoken_text TEXT, created REAL, expires REAL,
              acknowledged REAL, superseded INTEGER DEFAULT 0,
              wake_status TEXT DEFAULT 'pending', wake_attempts INTEGER DEFAULT 0,
              wake_after REAL DEFAULT 0, wake_error TEXT);
            CREATE INDEX IF NOT EXISTS message_queue ON messages(acknowledged,created);
        ''')
        columns={r[1] for r in self.db.execute('PRAGMA table_info(messages)')}
        for name,definition in {
            'audio_status':"TEXT DEFAULT 'pending'",'audio_attempts':'INTEGER DEFAULT 0',
            'audio_after':'REAL DEFAULT 0','audio_lease':'TEXT','audio_until':'REAL DEFAULT 0',
            'audio_error':'TEXT','audio_sent':'REAL'}.items():
            if name not in columns: self.db.execute('ALTER TABLE messages ADD COLUMN '+name+' '+definition)
        job_columns={r[1] for r in self.db.execute('PRAGMA table_info(jobs)')}
        for name,definition in {'input_source':"TEXT DEFAULT 'text'",'input_meta':"TEXT DEFAULT '{}'"}.items():
            if name not in job_columns:self.db.execute('ALTER TABLE jobs ADD COLUMN '+name+' '+definition)
        self.db.commit()
        self.memory=MemoryStore(self.db)
        self.routine=RoutineStore(self)

    def get_meta(self, key, default=None):
        with self.lock:
            r = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return json.loads(r[0]) if r else default

    def set_meta(self, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                            (key, json.dumps(value, ensure_ascii=False)))

    def online(self):
        return time.time() - self.get_meta('heartbeat', 0) < 65

    def add_message(self, job, kind, reply, spoken_text, expires=None):
        """Called within the job transaction: a result and its message commit together."""
        if kind == 'final':
            # An unread progress update is obsolete once its answer is ready.
            self.db.execute("UPDATE messages SET superseded=1 WHERE job_id=? AND kind='progress' AND acknowledged IS NULL", (job['id'],))
        self.db.execute('''INSERT OR IGNORE INTO messages
            (id,event_key,job_id,kind,reply,spoken_text,created,expires)
            VALUES (?,?,?,?,?,?,?,?)''', (uuid.uuid4().hex, job['id']+':'+kind,
            job['id'], kind, reply, spoken_text, time.time(),
            expires if expires is not None else time.time()+86400))

    def message(self, message_id=None):
        with self.lock:
            if message_id:
                row = self.db.execute('SELECT * FROM messages WHERE id=? AND superseded=0 AND expires>?', (message_id,time.time())).fetchone()
            else:
                row = self.db.execute('''SELECT * FROM messages WHERE acknowledged IS NULL
                    AND superseded=0 AND expires>? ORDER BY created,id LIMIT 1''', (time.time(),)).fetchone()
            return {k: row[k] for k in ('id','job_id','kind','reply','spoken_text','created','expires')} if row else None

    def acknowledge(self, message_id):
        with self.lock, self.db:
            if not self.db.execute('SELECT 1 FROM messages WHERE id=?',(message_id,)).fetchone():
                raise ValueError('Unknown message')
            self.db.execute('UPDATE messages SET acknowledged=coalesce(acknowledged,?) WHERE id=?', (time.time(),message_id))

    def progress(self, job_id, lease_token, text):
        if not isinstance(text,str) or not 1 <= len(text.strip()) <= 700:
            raise ValueError('Invalid progress message')
        with self.lock, self.db:
            job = self.job(job_id)
            if not job or job['status'] != 'running' or job['expires'] < time.time() or job['lease_until'] < time.time() or not hmac.compare_digest(job['lease_token'] or '',lease_token or ''):
                raise ValueError('Stale job lease')
            if job['kind'] == 'ask':
                self.add_message(job,'progress',text.strip(),text.strip(),min(job['expires'],time.time()+300))

    def recent_speech_notes(self,after=0):
        if type(after) not in (int,float) or after<0:raise ValueError('Invalid speech cutoff')
        with self.lock:
            rows=self.db.execute("SELECT m.id,m.job_id,m.spoken_text,m.created,j.input_source,j.input_meta FROM messages m JOIN jobs j ON j.id=m.job_id WHERE m.kind='speech' AND m.superseded=0 AND m.created>=? ORDER BY m.created DESC,m.id DESC",(after,))
            entries=[]
            for row in rows:
                meta=json.loads(row['input_meta'] or '{}')
                if row['input_source']=='routine' and meta.get('routine_phase') in ('morning','evening'):continue
                entries.append(dict(id=row['id'],text=row['spoken_text'],created=row['created']))
                if len(entries)==3:break
            return dict(entries=list(reversed(entries)))

    def history_search(self,job_id,lease_token,query):
        if not isinstance(query,str) or len(query)>500:raise ValueError('Invalid history query')
        with self.lock:
            job=self.job(job_id);now=time.time()
            if not job or job['status']!='running' or job['expires']<=now or job['lease_until']<=now or not hmac.compare_digest(job['lease_token'] or '',lease_token or ''):raise ValueError('Stale job lease')
            pattern='%'+query+'%'
            rows=self.db.execute("SELECT question,result,created FROM jobs WHERE conversation=? AND kind='ask' AND status='completed' AND (question LIKE ? OR result LIKE ?) ORDER BY created DESC LIMIT 5",(job['conversation'],pattern,pattern)).fetchall()
            return dict(turns=[dict(user=r['question'],assistant=(json.loads(r['result'] or '{}').get('reply') or json.loads(r['result'] or '{}').get('spoken_text','')),created=r['created']) for r in reversed(rows)])

    def speak(self, job_id, lease_token, text, request_id):
        """Enqueue an explicit tool call; return after commit, never after synthesis."""
        if not isinstance(text,str) or not 1<=len(text.strip())<=5000:
            raise ValueError('Expected 1 to 5000 characters of speech')
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9-]{36}',request_id):
            raise ValueError('Invalid speech request ID')
        text=text.strip();key=job_id+':speech:'+request_id
        with self.lock,self.db:
            job=self.job(job_id);now=time.time()
            if not job or job['status']!='running' or job['expires']<=now or job['lease_until']<=now or not hmac.compare_digest(job['lease_token'] or '',lease_token or ''):
                raise ValueError('Stale job lease')
            old=self.db.execute('SELECT id,spoken_text FROM messages WHERE event_key=?',(key,)).fetchone()
            if old:
                if old['spoken_text']!=text:raise ValueError('Speech request reused with different text')
                return dict(queued=True,message_id=old['id'])
            expires=min(job['expires'],now+(1800 if job['kind']=='briefing' else 86400))
            if job.get('input_source')=='routine':
                expires=min(expires,json.loads(job.get('input_meta') or '{}').get('delivery_deadline',expires))
            if expires<=now:raise ValueError('Speech expired')
            identifier=uuid.uuid4().hex
            self.db.execute('INSERT INTO messages (id,event_key,job_id,kind,reply,spoken_text,created,expires) VALUES (?,?,?,?,?,?,?,?)',(identifier,key,job_id,'speech',text,text,now,expires))
            return dict(queued=True,message_id=identifier)

    def enable_audio(self):
        with self.lock,self.db:
            self.db.execute("UPDATE messages SET audio_status='skipped' WHERE audio_status='pending'")
            self.set_meta('zello_enabled',True)
            self.set_meta('delivery_transport','zello')
            self.set_meta('push_enabled',False)

    def claim_audio(self):
        if not self.get_meta('zello_enabled',False): return None
        now=time.time()
        with self.lock,self.db:
            self.set_meta('audio_heartbeat',now)
            self.db.execute("UPDATE messages SET audio_status='pending',audio_lease=NULL WHERE audio_status='sending' AND audio_until<?",(now,))
            self.db.execute("UPDATE messages SET audio_status='skipped' WHERE audio_status='pending' AND (superseded=1 OR expires<=? OR acknowledged IS NOT NULL)",(now,))
            row=self.db.execute('''SELECT * FROM messages WHERE audio_status='pending'
                AND audio_after<=? AND kind IN ('final','progress','speech') AND audio_attempts<8
                ORDER BY created,id LIMIT 1''',(now,)).fetchone()
            if not row:return None
            token=secrets.token_urlsafe(24)
            self.db.execute("UPDATE messages SET audio_status='sending',audio_lease=?,audio_until=?,audio_attempts=audio_attempts+1 WHERE id=?",(token,now+240,row['id']))
            return dict(self.message(row['id']),lease_token=token)

    def renew_audio(self,message_id,token):
        with self.lock,self.db:
            row=self.db.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
            if not row or row['audio_status']!='sending' or row['audio_until']<time.time() or not hmac.compare_digest(row['audio_lease'] or '',token or ''):
                raise ValueError('Stale audio lease')
            self.db.execute('UPDATE messages SET audio_until=? WHERE id=?',(time.time()+240,message_id))
            self.set_meta('audio_heartbeat',time.time())

    def complete_audio(self,message_id,token,error=None):
        with self.lock,self.db:
            row=self.db.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
            if not row or row['audio_status']!='sending' or row['audio_until']<time.time() or not hmac.compare_digest(row['audio_lease'] or '',token or ''):
                raise ValueError('Stale audio lease')
            if error=='expired':
                self.db.execute("UPDATE messages SET audio_status='skipped',audio_error='expired',audio_lease=NULL WHERE id=?",(message_id,))
                return
            if error:
                self.db.execute('''UPDATE messages SET audio_status=?,audio_error=?,audio_after=?,audio_lease=NULL
                    WHERE id=?''',('failed' if row['audio_attempts']>=8 else 'pending',str(error)[:200],
                    time.time()+min(2**row['audio_attempts']*5,300),message_id))
            else:
                self.db.execute("UPDATE messages SET audio_status='sent',audio_sent=?,acknowledged=coalesce(acknowledged,?),audio_error=NULL,audio_lease=NULL WHERE id=?",(time.time(),time.time(),message_id))
                self.db.execute('UPDATE jobs SET delivered=1 WHERE id=?',(row['job_id'],))

    def expire_jobs(self):
        with self.lock, self.db:
            expired = self.db.execute("SELECT * FROM jobs WHERE status IN ('queued','running') AND expires<?", (time.time(),)).fetchall()
            for job in expired:
                error='任务已过期，请重新提问。'
                self.db.execute("UPDATE jobs SET status='expired',error=?,finished=? WHERE id=?",(error,time.time(),job['id']))
                if job['kind']=='ask':
                    self.add_message(job,'final',error,error)

    def enqueue(self, question, conversation='phone-main', kind='ask', dedup=None, input_source='text', input_metadata=None):
        now = time.time()
        with self.lock, self.db:
            if dedup:
                old = self.db.execute('SELECT * FROM jobs WHERE dedup=?', (dedup,)).fetchone()
                if old:
                    return dict(old)
            if kind == 'briefing' and input_source!='routine':
                old = self.db.execute("SELECT * FROM jobs WHERE kind='briefing' AND input_source!='routine' AND status IN ('queued','running') AND expires>?", (now,)).fetchone()
                if old:
                    return dict(old)
            elif kind=='ask' and self.db.execute("SELECT count(*) FROM jobs WHERE kind='ask' AND status IN ('queued','running') AND expires>?", (now,)).fetchone()[0] >= 8:
                raise ValueError('问题队列已满，请稍后再试。')
            job_id = uuid.uuid4().hex
            self.db.execute('INSERT INTO jobs(id,kind,conversation,question,status,created,expires,dedup,input_source,input_meta) VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (job_id, kind, conversation, question, 'queued', now,
                             now + (900 if kind == 'ask' else 1200), dedup,input_source,json.dumps(input_metadata or {},ensure_ascii=False)))
            self.queue_ready.notify_all()
            return self.job(job_id)

    def job(self, job_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def claim(self):
        now = time.time()
        with self.lock, self.db:
            self.expire_jobs()
            self.db.execute("UPDATE jobs SET status='queued',lease_token=NULL WHERE status='running' AND lease_until<? AND attempts<2", (now,))
            interrupted=self.db.execute("SELECT * FROM jobs WHERE status='running' AND lease_until<? AND attempts>=2",(now,)).fetchall()
            for job in interrupted:
                error='执行中断，请重新提问。'
                self.db.execute("UPDATE jobs SET status='failed',error=?,finished=? WHERE id=?",(error,now,job['id']))
                if job['kind']=='ask': self.add_message(job,'final',error,error)
            row = self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY CASE WHEN kind='ask' THEN 0 WHEN input_source='routine' THEN 1 ELSE 2 END,created LIMIT 1").fetchone()
            if not row:
                return None
            token = secrets.token_urlsafe(24)
            self.db.execute("UPDATE jobs SET status='running',started=?,lease_until=?,lease_token=?,attempts=attempts+1 WHERE id=?", (now, now+75, token, row['id']))
            history = self.db.execute("SELECT question,result FROM jobs WHERE conversation=? AND kind='ask' AND status='completed' ORDER BY finished DESC LIMIT 12", (row['conversation'],)).fetchall()
            memories = [] if self.get_meta('memory_authority')=='mac' else self.memory.active()
            job = self.job(row['id'])
            return dict(job=job, history=[dict(x) for x in reversed(history)],
                        memories=[x['text'] for x in memories],memory_records=memories,memory_retired= [] if self.get_meta('memory_authority')=='mac' else self.memory.retired_versions(),
                        profile=self.get_meta('profile', {'timezone': str(SG), 'calendar_connected': False}))

    def wait_for_task(self,seconds=0):
        deadline=time.monotonic()+max(0,min(15,float(seconds)))
        with self.queue_ready:
            while True:
                task=self.claim()
                remaining=deadline-time.monotonic()
                if task or remaining<=0:return task
                # claim() has committed its transaction before releasing the lock.
                self.queue_ready.wait(remaining)

    def heartbeat(self, payload):
        self.set_meta('heartbeat', time.time())
        self.set_meta('worker', {k: payload.get(k) for k in ('name', 'state', 'version', 'calendar_status', 'reasoning_effort', 'interactive_fast','memory_mode','voice_input_engine','codex_home_isolated')})
        if payload.get('job_id'):
            with self.lock, self.db:
                self.db.execute("UPDATE jobs SET lease_until=? WHERE id=? AND status='running' AND lease_token=?", (time.time()+75, payload['job_id'], payload.get('lease_token')))

    def complete(self, job_id, lease_token, result=None, error=None, transcript=None):
        with self.lock, self.db:
            job = self.job(job_id)
            if not job or job['status'] != 'running' or not hmac.compare_digest(job['lease_token'] or '', lease_token or '') or job['expires'] < time.time():
                raise ValueError('Stale job lease')
            if job.get('input_source')=='zello_audio' and not error:
                if not isinstance(transcript,str) or not 1<=len(transcript.strip())<=8000:raise ValueError('Missing native transcript')
                self.db.execute('UPDATE jobs SET question=? WHERE id=?',(transcript.strip(),job_id))
                job['question']=transcript.strip()
            elif transcript is not None:
                raise ValueError('Transcript only allowed for native voice jobs')
            if error:
                self.db.execute("UPDATE jobs SET status='failed',error=?,finished=? WHERE id=?", (str(error)[:600], time.time(), job_id))
                if job['kind']=='ask': self.add_message(job,'final','助手执行失败，请稍后重试。','助手执行失败，请稍后重试。')
                return
            tool_mode=isinstance(result,dict) and result.get('delivery_mode')=='tool'
            if tool_mode:
                if not isinstance(result.get('reply'),str) or len(result['reply'])>30000:raise ValueError('Invalid text response')
                spoken=[r[0] for r in self.db.execute("SELECT spoken_text FROM messages WHERE job_id=? AND kind='speech' ORDER BY created,id",(job_id,))]
                result=dict(reply=result['reply'].strip(),spoken_text='\n'.join(spoken),should_notify=False,sources=[],memory_updates=[],delivery_mode='tool')
            else:
                if not isinstance(result, dict) or not isinstance(result.get('reply'), str) or not result['reply'].strip() or len(result['reply']) > 30000:
                    raise ValueError('Invalid agent reply')
                if not isinstance(result.get('spoken_text'), str) or not result['spoken_text'].strip() or len(result['spoken_text']) > 5000 or not isinstance(result.get('should_notify'), bool):
                    raise ValueError('Invalid spoken response')
            self.db.execute("UPDATE jobs SET status='completed',result=?,finished=? WHERE id=?", (json.dumps(result, ensure_ascii=False), time.time(), job_id))
            if not tool_mode and (job['kind']=='ask' or result['should_notify']):
                expires=time.time()+(86400 if job['kind']=='ask' else 1800)
                if job['kind']=='briefing' and result.get('valid_until'):
                    try:
                        until=dt.datetime.fromisoformat(result['valid_until'])
                        if until.tzinfo: expires=min(expires,until.timestamp())
                    except (ValueError,TypeError): pass
                if job.get('input_source')=='routine':expires=min(expires,json.loads(job.get('input_meta') or '{}').get('delivery_deadline',expires))
                self.add_message(job,'final',result['reply'],result['spoken_text'],expires)
            if self.get_meta('memory_authority')=='mac':return dict(memory_authority='mac')
            report=self.memory.apply(result.get('memory_updates',[]),job['question'],job_id,job['kind'])
            return dict(memory_report=report,memory_records=self.memory.active())

    def public_job(self, job):
        result = json.loads(job['result']) if job.get('result') else {}
        expired = job['status'] in ('queued', 'running') and job['expires'] < time.time()
        status = 'expired' if expired else job['status']
        done = status in ('completed', 'failed', 'expired')
        online = self.online()
        spoken = result.get('spoken_text') or job.get('error') or ('问题已过期，请重新提问。' if expired else '助手正在思考，请稍候。')
        if not done and not online:
            spoken = 'Mac 助手暂时离线。问题已保存，恢复连接后可以运行“听助手回复”。'
        return dict(id=job['id'], status=status,
                    done=done,
                    delivery_status='ready' if done or not online else 'waiting',
                    reply=result.get('reply', ''),
                    spoken_text=spoken,
                    sources=result.get('sources', []), created=job['created'],
                    should_notify=result.get('should_notify', False),
                    worker_online=online)

    def latest(self, kind='ask', conversation='phone-main'):
        with self.lock:
            r = self.db.execute('SELECT * FROM jobs WHERE kind=? AND conversation=? ORDER BY created DESC LIMIT 1', (kind, conversation)).fetchone()
            return dict(r) if r else None

    def briefing(self, proactive=False):
        job = self.latest('briefing', 'briefing')
        valid_until = 0
        if job and job['status'] == 'completed':
            cached = json.loads(job['result'])
            valid_until = (job['finished'] or 0) + 1800
            if cached.get('valid_until'):
                try:
                    parsed = dt.datetime.fromisoformat(cached['valid_until'])
                    if parsed.tzinfo:
                        valid_until = min(parsed.timestamp(), (job['finished'] or 0) + 3600)
                except (ValueError, TypeError):
                    pass
        if not job or job['status'] != 'completed' or time.time() > valid_until:
            self.enqueue('检查现在有什么值得我注意的事情。', 'briefing', 'briefing')
            return '' if proactive else '提醒。简报正在更新。稍后再来听听。' if self.online() else '提醒。Mac 助手暂时离线，恢复连接后会更新简报。'
        result = json.loads(job['result'])
        if proactive:
            if job['delivered'] or not result.get('should_notify'):
                return ''
            with self.lock, self.db:
                # Claim delivery atomically: concurrent polls cannot repeat it.
                changed = self.db.execute('UPDATE jobs SET delivered=1 WHERE id=? AND delivered=0', (job['id'],)).rowcount
                if not changed:
                    return ''
        return '提醒。' + result['spoken_text'].removeprefix('提醒。')

    def scheduled_tick(self):
        self.expire_jobs()
        self.routine.tick()
        now = dt.datetime.now(SG)
        if self.get_meta('hourly_enabled',False) and 8 <= now.hour < 23 and not self.routine.nearby():
            self.enqueue('定时生活检查：仅在有新信息、值得打扰时建议提醒。', 'briefing', 'briefing', 'hour:' + now.strftime('%Y-%m-%dT%H'))
        with self.lock, self.db:
            self.db.execute('DELETE FROM jobs WHERE created<?', (time.time()-30*86400,))
            self.db.execute('DELETE FROM messages WHERE created<?', (time.time()-30*86400,))

    def weather_tick(self):
        if not self.get_meta('singapore_weather_enabled',False):
            return
        if time.time() - self.get_meta('weather_checked', 0) < 600:
            return
        self.set_meta('weather_checked', time.time())
        # A cheap data check triggers agent reasoning only on meaningful change.
        # No claim about the user's exact location follows from nationwide data.
        url = 'https://api-open.data.gov.sg/v2/real-time/api/two-hr-forecast'
        with urlopen(Request(url, headers={'User-Agent': 'PersonalAgent/1.0'}), timeout=8) as response:
            data = json.load(response)
        item = data['data']['items'][-1]
        end = dt.datetime.fromisoformat(item['valid_period']['end']).timestamp()
        if end < time.time():
            return
        descriptions = [x['forecast'].lower() for x in item['forecasts']]
        current = dict(issued=item['update_timestamp'],
                       wet=sum('rain' in x or 'shower' in x for x in descriptions)/len(descriptions),
                       severe=any('thunder' in x or 'heavy' in x for x in descriptions))
        previous = self.get_meta('weather_snapshot')
        self.set_meta('weather_snapshot', current)
        if not previous or current['issued'] == previous['issued']:
            return
        changed = (current['severe'] and not previous['severe']) or (current['wet'] >= .35 and previous['wet'] < .15)
        now = dt.datetime.now(SG)
        if changed and (8 <= now.hour < 23 or current['severe']) and time.time()-self.get_meta('weather_triggered',0)>1800:
            self.enqueue('天气预报出现显著变化，请自主核实是否值得提醒用户。官方预报更新时间：'+current['issued']+'；来源：'+url, 'briefing', 'briefing', 'weather:'+current['issued'])
            self.set_meta('weather_triggered',time.time())


class Handler(BaseHTTPRequestHandler):
    server_version = 'PersonalAgent/1'

    def log_message(self, fmt, *args):
        # Do not log question text, headers, tokens or query strings.
        if '/bark/' in urlparse(self.path).path:
            return
        if urlparse(self.path).path in ('/worker/claim', '/worker/heartbeat') and len(args) > 1 and str(args[1]) == '200':
            return
        print(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), self.command,
              urlparse(self.path).path, args[1] if len(args)>1 else '', flush=True)

    def respond(self, status, value, plain=False):
        body = (value if plain else json.dumps(value, ensure_ascii=False)).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', ('text/plain' if plain else 'application/json') + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        store = self.server.store
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith(PREFIX + '/'):
            path = path[len(PREFIX):]
        delivery=getattr(self.server,'delivery',None)
        if delivery and path.startswith('/bark/'):
            try:
                if delivery.proxy(self,path,parsed.query): return
            except (ValueError,TypeError):
                return self.respond(400,{'code':400,'message':'Invalid request'})
        worker = path.startswith('/worker/')
        expected = store.worker_token if worker else store.client_token
        if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + expected):
            return self.respond(401, {'error': 'Unauthorized'})
        query = parse_qs(parsed.query)
        try:
            payload = {}
            if self.command == 'POST':
                length = int(self.headers.get('Content-Length', 0))
                if length <= 0 or length > (524288 if path=='/wellness/ingest' else 65536):
                    return self.respond(413, {'error': 'Invalid body size'})
                raw = self.rfile.read(length).decode('utf-8')
                payload = json.loads(raw) if 'application/json' in self.headers.get('Content-Type', '') else {'text': raw}
                if not isinstance(payload, dict):
                    raise ValueError('Expected a JSON object')
            if self.command=='POST' and path=='/worker/speech-notes/recent':return self.respond(200,store.recent_speech_notes(payload.get('after',0)))
            if self.command=='POST' and path=='/worker/history/search':return self.respond(200,store.history_search(payload['job_id'],payload['lease_token'],payload['query']))
            if self.command=='POST' and path=='/worker/speak':return self.respond(202,store.speak(payload['job_id'],payload['lease_token'],payload['text'],payload['request_id']))
            if self.command=='POST' and path=='/routine/signal':return self.respond(200,store.routine.signal(payload))
            if self.command=='GET' and path=='/routine/status':return self.respond(200,dict(config=store.routine.config()))
            if self.command=='POST' and path=='/wellness/ingest':return self.respond(202,store.routine.ingest(payload))
            if self.command=='POST' and path=='/worker/wellness/pull':return self.respond(200,store.routine.pull())
            if self.command=='POST' and path=='/worker/wellness/ack':return self.respond(200,store.routine.ack(payload.get('id','')))
            if conversation_dispatch(self,store,path,query,payload):return
            if self.command == 'GET' and path == '/health':
                return self.respond(200, dict(ok=True, worker_online=store.online(), worker=store.get_meta('worker'), delivery_transport=store.get_meta('delivery_transport','not_configured'),push_registered=bool(store.get_meta('bark_device_key')),voice_delivery_enabled=store.get_meta('zello_enabled',False),voice_delivery_online=time.time()-store.get_meta('audio_heartbeat',0)<180,time=dt.datetime.now(SG).isoformat()))
            if self.command=='GET' and path=='/memories':
                with store.lock:
                    if store.get_meta('memory_authority')=='mac':return self.respond(200,dict(authority='mac',memories=[],archived_backup_count=store.db.execute('SELECT count(*) FROM memory_items').fetchone()[0],notice='Current memory is available in the Mac local viewer.'))
                    return self.respond(200,{'memories':store.memory.active()})
            if self.command=='POST' and path=='/delivery/zello/enable':
                store.enable_audio()
                return self.respond(200,{'ok':True})
            if self.command=='POST' and path in ('/push/test','/push/enable') and delivery:
                delivery.test() if path.endswith('/test') else delivery.enable()
                return self.respond(202,{'ok':True})
            if self.command == 'GET' and (path=='/messages/next' or re.fullmatch('/messages/[a-f0-9]{32}',path)):
                return self.respond(200, {'message':store.message(None if path.endswith('/next') else path.split('/')[-1])})
            if self.command == 'POST' and re.fullmatch('/messages/[a-f0-9]{32}/ack',path):
                store.acknowledge(path.split('/')[2])
                return self.respond(200,{'ok':True})
            if self.command == 'POST' and path == '/ask':
                question = payload.get('text', '').strip()
                conversation = payload.get('conversation', 'phone-main')
                if not question or len(question) > 8000 or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', conversation):
                    raise ValueError('Invalid question or conversation')
                if self.headers.get('X-New-Conversation') == '1':
                    conversation = 'phone-' + uuid.uuid4().hex[:12]
                    store.set_meta('phone_conversation', conversation)
                elif conversation == 'phone-main':
                    conversation = store.get_meta('phone_conversation', 'phone-main')
                job = store.enqueue(question, conversation, dedup=self.headers.get('Idempotency-Key'),input_source=payload.get('input_source') if payload.get('input_source') in ('web','zello_review') else 'shortcut')
                return self.respond(202, store.public_job(job))
            if self.command == 'POST' and path == '/conversation/new':
                store.set_meta('phone_conversation', 'phone-' + uuid.uuid4().hex[:12])
                return self.respond(200, '提醒。已开始新对话。接下来可以运行“问生活助手”。', True)
            if self.command == 'GET' and path == '/answer':
                conversation = store.get_meta('phone_conversation', 'phone-main')
                job = store.latest(conversation=conversation)
                if not job:
                    return self.respond(200, '提醒。还没有提问记录。你可以先运行“问生活助手”。', True)
                info = store.public_job(job)
                text = info['spoken_text']
                if not info['done']:
                    text = '助手正在思考，请稍后运行“听助手回复”。' if store.online() else 'Mac 助手暂时离线，问题已保存，恢复连接后会处理。'
                return self.respond(200, '提醒。' + text.removeprefix('提醒。'), True)
            if self.command == 'GET' and re.fullmatch('/jobs/[a-f0-9]{32}(/speech)?', path):
                job = store.job(path.split('/')[2])
                if not job:
                    return self.respond(404, {'error': 'Unknown job'})
                until = time.monotonic() + max(0, min(20, float(query.get('wait', ['0'])[0])))
                while job['status'] in ('queued', 'running') and job['expires'] > time.time() and time.monotonic() < until:
                    time.sleep(.4)
                    job = store.job(job['id'])
                info = store.public_job(job)
                if path.endswith('/speech'):
                    text = ('提醒。' + info['spoken_text'].removeprefix('提醒。')) if info['done'] else ''
                    if not info['done'] and not store.online():
                        text = '提醒。Mac 助手暂时离线。问题已保存，恢复连接后可以运行“听助手回复”。'
                    return self.respond(200, text, True)
                return self.respond(200, info)
            if self.command == 'GET' and path == '/briefing':
                return self.respond(200, store.briefing(query.get('proactive') == ['1']), True)
            if self.command == 'POST' and path == '/briefing/refresh':
                return self.respond(202, store.public_job(store.enqueue('用户请求现在更新生活简报。', 'briefing', 'briefing')))
            if self.command == 'POST' and path == '/worker/heartbeat':
                store.heartbeat(payload)
                return self.respond(200, {'ok': True})
            if self.command == 'POST' and path == '/worker/claim':
                return self.respond(200, {'task': store.wait_for_task(payload.get('wait',0))})
            if self.command == 'POST' and path == '/worker/complete':
                report=store.complete(payload['job_id'], payload['lease_token'], payload.get('result'), payload.get('error'),payload.get('transcript'))
                return self.respond(200, dict(ok=True,**(report or {})))
            if self.command == 'POST' and path == '/worker/progress':
                store.progress(payload['job_id'],payload['lease_token'],payload['text'])
                return self.respond(200,{'ok':True})
            if self.command=='POST' and path=='/worker/messages/claim':
                return self.respond(200,{'message':store.claim_audio()})
            if self.command=='POST' and path=='/worker/messages/complete':
                store.complete_audio(payload['message_id'],payload['lease_token'],payload.get('error'))
                return self.respond(200,{'ok':True})
            if self.command=='POST' and path=='/worker/messages/heartbeat':
                store.renew_audio(payload['message_id'],payload['lease_token'])
                return self.respond(200,{'ok':True})
            return self.respond(404, {'error': 'Unknown endpoint'})
        except (ValueError, TypeError, KeyError) as exc:
            self.respond(400, {'error': str(exc)[:200]})
        except Exception as exc:
            print('server_error', type(exc).__name__, flush=True)
            self.respond(500, {'error': 'Service error'})


def scheduler(store):
    while True:
        try:
            store.scheduled_tick()
            store.weather_tick()
        except Exception as exc:
            print('schedule_error', type(exc).__name__, flush=True)
        time.sleep(30)


def main():
    os.umask(0o077)
    config = json.loads(Path(os.environ.get('PERSONAL_AGENT_CONFIG', '/etc/personal-agent/config.json')).read_text())
    Path(config['database']).parent.mkdir(parents=True,exist_ok=True)
    store = Store(config['database'], config['client_token'], config['worker_token'])
    for key in ('profile','hourly_enabled','singapore_weather_enabled','memory_authority','routine_wind_down_minutes','routine_daily_plan'):
        if key in config:store.set_meta(key,config[key])
    server = ThreadingHTTPServer((config.get('host','127.0.0.1'), config.get('port', 18864)), Handler)
    server.store = store
    if config.get('bark'):
        from bark_transport import BarkDelivery
        server.delivery=BarkDelivery(store,config['bark'])
        threading.Thread(target=server.delivery.run,daemon=True).start()
    threading.Thread(target=scheduler, args=(store,), daemon=True).start()
    print('personal-agent broker ready', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
