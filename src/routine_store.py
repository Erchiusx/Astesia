"""Sleep-plan event scheduling and bounded Health relay, sharing broker transactions."""
import datetime as dt,hashlib,json,time,uuid
from zoneinfo import ZoneInfo
from runtime_paths import TIMEZONE as SG
METRICS={'sleep':200,'steps':1000,'active_calories':500,'exercise_minutes':500,'heart_rate':50,'resting_heart_rate':7}
FIELDS={'values','starts','ends','sources','units'}

def validate_health(value):
 if not isinstance(value,dict):raise ValueError('Expected health object')
 allowed={'schema_version','captured_at'}|{m+'_'+f for m in METRICS for f in FIELDS}
 if set(value)-allowed:raise ValueError('Unsupported health fields')
 if str(value.get('schema_version'))!='1':raise ValueError('Unsupported health schema')
 try:captured=dt.datetime.fromisoformat(value['captured_at'].replace('Z','+00:00'))
 except Exception:raise ValueError('Invalid captured_at')
 if captured.tzinfo is None or not time.time()-86400<=captured.timestamp()<=time.time()+300:raise ValueError('Health timestamp outside relay window')
 result={'schema_version':1,'captured_at':captured.isoformat()}
 for metric in METRICS:
  for field in FIELDS:
   key=metric+'_'+field;v=value.get(key,'')
   if not isinstance(v,str) or len(v)>60000:raise ValueError('Invalid health field '+key)
   result[key]=v
 if len(json.dumps(result,ensure_ascii=False).encode())>400000:raise ValueError('Health data too large')
 return result

class RoutineStore:
 def __init__(self,store):
  self.store=store;self.db=store.db
  self.db.executescript('''CREATE TABLE IF NOT EXISTS routine_events(
   id TEXT PRIMARY KEY, phase TEXT, occurred REAL, due REAL, deadline REAL,
   status TEXT, job_id TEXT, metadata TEXT);
   CREATE TABLE IF NOT EXISTS wellness_pending(id TEXT PRIMARY KEY,received REAL,payload TEXT);
  ''');self.db.commit()
 def config(self):
  return dict(wake_delay_minutes=30,bedtime_lead_minutes=5,wind_down_minutes=self.store.get_meta('routine_wind_down_minutes'),source='user_daily_sleep_plan' if self.store.get_meta('routine_daily_plan',{}).get('enabled') else 'iphone_health_sleep_schedule',daily_plan=self.store.get_meta('routine_daily_plan'))
 def signal(self,payload):
  event=payload.get('event','')
  if event in ('',None,'test'):return dict(ok=True,scheduled=False,preview=True,config=self.config())
  if event not in ('wake','wind_down'):raise ValueError('Unsupported sleep event')
  now=time.time();cfg=self.config();phase='morning' if event=='wake' else 'evening'
  if event=='wind_down' and cfg['wind_down_minutes'] is None:return dict(ok=True,scheduled=False,reason='wind_down_duration_not_configured',config=cfg)
  if event=='wake':due=now+cfg['wake_delay_minutes']*60;deadline=due+15*60;target=now
  else:
   minutes=cfg['wind_down_minutes']
   if type(minutes)!=int or not 5<=minutes<=180:raise ValueError('Invalid wind down duration')
   target=now+minutes*60;due=target-cfg['bedtime_lead_minutes']*60;deadline=target
  day=dt.datetime.fromtimestamp(target,SG).date().isoformat();key=phase+':'+day
  metadata=dict(routine_phase=phase,signal=event,source=cfg['source'],scheduled_for=dt.datetime.fromtimestamp(due,SG).isoformat(),delivery_deadline=deadline,bedtime=dt.datetime.fromtimestamp(target,SG).isoformat() if phase=='evening' else None)
  with self.store.lock,self.db:
   old=self.db.execute('SELECT * FROM routine_events WHERE id=?',(key,)).fetchone()
   if old:return dict(ok=True,scheduled=False,duplicate=True,phase=phase,due=dt.datetime.fromtimestamp(old['due'],SG).isoformat())
   self.db.execute('INSERT INTO routine_events VALUES (?,?,?,?,?,?,?,?)',(key,phase,now,due,deadline,'pending',None,json.dumps(metadata)))
  return dict(ok=True,scheduled=True,phase=phase,due=metadata['scheduled_for'])
 def seed_daily_plan(self,now):
  plan=self.store.get_meta('routine_daily_plan',{})
  if not plan.get('enabled'):return
  zone=ZoneInfo(plan['timezone']);today=dt.datetime.fromtimestamp(now,zone).date()
  def clock(key):
   value=plan[key]
   if not isinstance(value,str) or len(value)!=5:raise ValueError('Invalid daily sleep time')
   parsed=dt.time.fromisoformat(value)
   if parsed.tzinfo is not None:raise ValueError('Daily sleep time must be local')
   return parsed
  wake=clock('wake');bed=clock('bedtime')
  with self.store.lock,self.db:
   for offset in (0,1):
    day=today+dt.timedelta(days=offset)
    for phase,at,delta in [('morning',wake,30),('evening',bed,-5)]:
     target=dt.datetime.combine(day,at,tzinfo=zone);due=(target+dt.timedelta(minutes=delta)).timestamp()
     if due<=now:continue  # Never invent a missed reminder after enabling/restarting.
     deadline=due+900 if phase=='morning' else target.timestamp()
     key=phase+':'+day.isoformat()
     metadata=dict(routine_phase=phase,signal='daily_plan',source='user_daily_sleep_plan',scheduled_for=dt.datetime.fromtimestamp(due,zone).isoformat(),delivery_deadline=deadline,bedtime=target.isoformat() if phase=='evening' else None,plan=plan)
     self.db.execute('INSERT OR IGNORE INTO routine_events VALUES (?,?,?,?,?,?,?,?)',(key,phase,now,due,deadline,'pending',None,json.dumps(metadata)))
 def tick(self):
  now=time.time()
  self.seed_daily_plan(now)
  with self.store.lock,self.db:
   self.db.execute("UPDATE routine_events SET status='expired' WHERE status='pending' AND deadline<=?",(now,))
   for row in self.db.execute("SELECT * FROM routine_events WHERE status='pending' AND due<=? AND deadline>? ORDER BY due",(now,now)).fetchall():
    meta=json.loads(row['metadata']);phase=row['phase']
    question=('早间简报：现在是睡眠计划的起床时间约半小时后。简短检查今天的日程、重要提醒事项、出行天气和已同步健康摘要。' if phase=='morning' else '睡前简报：现在距离睡眠计划的就寝时间约五分钟。简短检查明早安排、需要今晚准备的事项和已同步健康摘要，不做长篇思考或制造焦虑。')+'这是用户主动订阅的每日简报；给一条简洁、有实际内容的语音，不把计划起床当作实际醒来。'
    job=self.store.enqueue(question,'routine-'+phase,'briefing','routine:'+row['id'],input_source='routine',input_metadata=meta)
    self.db.execute('UPDATE jobs SET expires=? WHERE id=?',(row['deadline'],job['id']))
    self.db.execute("UPDATE routine_events SET status='queued',job_id=? WHERE id=?",(job['id'],row['id']))
   self.db.execute('DELETE FROM wellness_pending WHERE received<?',(now-86400,))
   self.db.execute('DELETE FROM routine_events WHERE occurred<?',(now-30*86400,))
 def nearby(self):
  now=time.time()
  with self.store.lock:
   return bool(self.db.execute("SELECT 1 FROM routine_events WHERE status IN ('pending','queued') AND due BETWEEN ? AND ? AND deadline>? LIMIT 1",(now-1800,now+900,now)).fetchone())
 def ingest(self,payload):
  value=validate_health(payload);encoded=json.dumps(value,ensure_ascii=False);identifier=hashlib.sha256(encoded.encode()).hexdigest()
  with self.store.lock,self.db:
   self.db.execute('DELETE FROM wellness_pending');self.db.execute('INSERT INTO wellness_pending VALUES (?,?,?)',(identifier,time.time(),encoded))
  return dict(ok=True,queued=True,id=identifier)
 def pull(self):
  with self.store.lock:
   row=self.db.execute('SELECT * FROM wellness_pending WHERE received>? ORDER BY received DESC LIMIT 1',(time.time()-86400,)).fetchone()
   return dict(snapshot=dict(id=row['id'],received=row['received'],payload=json.loads(row['payload'])) if row else None)
 def ack(self,identifier):
  with self.store.lock,self.db:self.db.execute('DELETE FROM wellness_pending WHERE id=?',(identifier,))
  return {'ok':True}
