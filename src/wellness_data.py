"""Read-only normalization of user-authorized iPhone Health exports."""
import datetime as dt,json,math,pathlib,re,time
from routine_store import METRICS,FIELDS
from runtime_paths import TIMEZONE as SG

def _lines(v):return [x.strip() for x in str(v or '').splitlines() if x.strip()]
def _number(v):
 s=str(v).strip().replace(',','')
 m=re.fullmatch(r'([-+]?\d+(?:\.\d+)?)\s*([^\d]*)',s)
 return float(m[1]) if m else None

def records(payload,metric):
 fields={f:_lines(payload.get(metric+'_'+f,'')) for f in FIELDS};n=len(fields['values'])
 if not n:return dict(status='no_samples',records=[],possibly_truncated=False)
 for key in ['starts','ends','sources','units']:
  if not fields[key] and key=='units':fields[key]=['']*n
  if len(fields[key])==1 and n>1:fields[key]*=n
  if len(fields[key])!=n:return dict(status='unparseable',reason='Health detail arrays have different lengths',records=[],possibly_truncated=n>=METRICS[metric])
 out=[]
 for i in range(n):
  try:
   start=dt.datetime.fromisoformat(fields['starts'][i].replace('Z','+00:00'));end=dt.datetime.fromisoformat(fields['ends'][i].replace('Z','+00:00'))
   if start.tzinfo is None or end.tzinfo is None or end<start:raise ValueError()
   out.append(dict(value=fields['values'][i],number=_number(fields['values'][i]),unit=fields['units'][i],source=fields['sources'][i],start=start.isoformat(),end=end.isoformat()))
  except (ValueError,OverflowError):continue
 return dict(status='available' if out else 'unparseable',records=out,possibly_truncated=n>=METRICS[metric],sample_count=len(out))

def _sleep_summary(items):
 if not items:return []
 latest=max(dt.datetime.fromisoformat(r['end']).timestamp() for r in items);sources={}
 for r in items:
  start=dt.datetime.fromisoformat(r['start']).timestamp();end=dt.datetime.fromisoformat(r['end']).timestamp()
  if end<latest-18*3600:continue
  label=r['value'].casefold().strip();numeric=r['number']
  awake=any(w in label for w in ['awake','in bed','清醒','在床','卧床']) or numeric in [0,2]
  asleep=any(w in label for w in ['asleep','rem','deep','core','深睡','深度','核心','快速眼动','睡着']) or numeric in [1,3,4,5]
  source=sources.setdefault(r['source'],dict(intervals=[],unclassified=0))
  if asleep and not awake:source['intervals'].append((start,end))
  elif not awake:source['unclassified']+=1
 out=[]
 for name,value in sources.items():
  intervals=sorted(set(value['intervals']));merged=[]
  for start,end in intervals:
   if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
   else:merged.append((start,end))
  out.append(dict(source=name,recognized_asleep_hours=round(sum(b-a for a,b in merged)/3600,2) if merged else None,unclassified_samples=value['unclassified'],latest_record_end=dt.datetime.fromtimestamp(latest,SG).isoformat()))
 return out

def summarize(payload):
 output={}
 for metric in METRICS:
  block=records(payload,metric);items=block.pop('records');entry=dict(block)
  if items:
   entry['latest']=max(items,key=lambda x:x['end'])
   if metric=='sleep':entry['by_source']=_sleep_summary(items)
   elif metric in ['steps','active_calories','exercise_minutes']:
    today=dt.datetime.now(SG).date();groups={}
    for row in items:
     if dt.datetime.fromisoformat(row['start']).astimezone(SG).date()!=today or row['number'] is None:continue
     key=(row['source'],row['unit']);groups[key]=groups.get(key,0)+row['number']
    entry['today_observed_by_source']=[dict(source=k[0],unit=k[1],observed_sum=round(v,2),is_health_app_daily_total=False) for k,v in groups.items()]
   entry['quality']='Observed samples only; do not combine sources or infer missing data as zero.'
  output[metric]=entry
 return output

class WellnessSnapshot:
 def __init__(self,root):self.root=pathlib.Path(root)
 def load(self):
  p=self.root/'latest.json'
  if not p.exists():return dict(status='not_connected',metrics={})
  value=json.loads(p.read_text());captured=dt.datetime.fromisoformat(value['payload']['captured_at']);age=time.time()-captured.timestamp()
  return dict(status='available' if 0<=age<=36*3600 else 'stale',captured_at=captured.isoformat(),age_hours=round(age/3600,2),received_at=value.get('received_at'),metrics=summarize(value['payload']))
 def install(self,envelope):
  import os
  self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
  value=dict(received_at=dt.datetime.now(dt.timezone.utc).isoformat(),relay_id=envelope['id'],payload=envelope['payload'])
  p=self.root/'latest.json';tmp=self.root/'latest.json.tmp'
  with tmp.open('w') as out:json.dump(value,out,ensure_ascii=False);out.flush();os.fsync(out.fileno())
  tmp.chmod(0o600);tmp.replace(p)
  history=self.root/'history';history.mkdir(mode=0o700,exist_ok=True);day=dt.datetime.fromisoformat(envelope['payload']['captured_at']).astimezone(SG).date().isoformat();target=history/(day+'.json');temp=target.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False));temp.chmod(0o600);temp.replace(target)
  for old in sorted(history.glob('*.json'),reverse=True)[7:]:old.unlink()
