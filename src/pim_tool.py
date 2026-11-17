"""Structured, task-scoped local tools for Calendar, Reminders and Notes."""
import argparse,datetime as dt,hashlib,json,os,pathlib,time,uuid
ACTIONS={'system':{'status'},'calendar':{'calendars','list','get','create','update','delete'},'reminders':{'lists','list','get','create','update','delete'},'notes':{'folders','list','get','create','update','delete'}}
MUTATIONS={'create','update','delete'}
COMMON={'id','expected_modified'}
FIELDS={
 ('calendar','list'):{'start','end','calendar_id','query'},('calendar','create'):{'title','start','end','all_day','location','notes','calendar_id','alarm_minutes_before'},('calendar','update'):COMMON|{'title','start','end','all_day','location','notes','calendar_id','alarm_minutes_before'},
 ('reminders','list'):{'list_id','query','include_completed'},('reminders','create'):{'title','notes','list_id','due','completed'},('reminders','update'):COMMON|{'title','notes','list_id','due','completed'},
 ('notes','list'):{'folder_id','query'},('notes','create'):{'title','body','folder_id'},('notes','update'):COMMON|{'title','body','mode'},
}
def validate(domain,action,params):
 if domain not in ACTIONS or action not in ACTIONS[domain]:raise ValueError('Unsupported domain/action')
 if not isinstance(params,dict):raise ValueError('Params must be an object')
 allowed=FIELDS.get((domain,action),COMMON if action=='delete' else {'id'} if action=='get' else set())
 if set(params)-allowed:raise ValueError('Unknown parameters: '+','.join(sorted(set(params)-allowed)))
 for key,value in params.items():
  if key in {'title','id','calendar_id','list_id','folder_id','query','location','notes','body','mode'} and (not isinstance(value,str) or len(value)>(20000 if key in {'body','notes'} else 1000)):raise ValueError('Invalid '+key)
  if key in {'all_day','completed','include_completed'} and type(value)!=bool:raise ValueError('Invalid '+key)
  if key in {'expected_modified','alarm_minutes_before'} and value is not None and (type(value) not in {int,float} or value<0):raise ValueError('Invalid '+key)
 for key in ['start','end','due']:
  if key not in params or params[key] is None:continue
  s=params[key]
  if not isinstance(s,str):raise ValueError('Invalid '+key)
  parsed=dt.datetime.fromisoformat(s.replace('Z','+00:00'))
  if len(s)!=10 and parsed.tzinfo is None:raise ValueError(key+' needs an explicit timezone')
 if action in {'get','update','delete'} and not params.get('id'):raise ValueError('Exact id is required')
 if action in {'update','delete'} and type(params.get('expected_modified')) not in {int,float}:raise ValueError('Read the item first; expected_modified is required')
 if action=='create' and not params.get('title'):raise ValueError('Title is required')
 if domain=='calendar' and action in {'list','create'}:
  if not params.get('start') or not params.get('end'):raise ValueError('Start and exclusive end are required')
 if domain=='notes' and params.get('mode','append') not in {'append','replace'}:raise ValueError('Mode must be append or replace')
 if action=='update' and set(params)<=COMMON:raise ValueError('No update fields supplied')
 if domain=='notes' and action=='create' and 'body' not in params:raise ValueError('Body is required')
 return params

def _trash_note_ids(workspace,query,timeout):
 folders=call('notes','folders',workspace=workspace,timeout=timeout)
 if not folders.get('ok'):raise ValueError('Could not verify Notes recovery folders')
 ids=set()
 for folder in folders['data']['folders']:
  if folder['title'].casefold() not in {'recently deleted','最近删除','最近刪除'}:continue
  value=call('notes','list',{'folder_id':folder['id'],'query':query},workspace=workspace,timeout=timeout)
  if not value.get('ok'):raise ValueError('Could not verify Notes recovery state')
  ids.update(n['id'] for n in value['data']['notes'])
 return ids

def _filter_note_response(value,action,params,workspace,timeout):
 if not value.get('ok'):return value
 if action=='list' and not params.get('folder_id'):
  deleted=_trash_note_ids(workspace,params.get('query',''),timeout)
  rows=value['data']['notes'];kept=[n for n in rows if n['id'] not in deleted]
  value=dict(value,data=dict(value['data'],notes=kept,excluded_recently_deleted=len(rows)-len(kept)))
 elif action=='get':
  item=value['data'];deleted=_trash_note_ids(workspace,item.get('title',''),timeout)
  value=dict(value,data=dict(item,in_recently_deleted=item['id'] in deleted))
 return value

def call(domain,action,params=None,evidence='',workspace=None,timeout=145,request_id=None):
 os.umask(0o077);params=validate(domain,action,params or {});workspace=pathlib.Path(workspace or pathlib.Path.cwd()).resolve();queue=workspace/'pim-requests';queue.mkdir(mode=0o700,exist_ok=True);context={}
 if action in MUTATIONS:
  context=json.loads((workspace/'pim-task.json').read_text())
  if context.get('kind')!='ask' or time.time()-context.get('created',0)>900:raise ValueError('Writes require a current interactive user task')
  if not evidence.strip() or evidence not in context.get('question',''):raise ValueError('Evidence must quote the current user request exactly')
 # Keep bounded receipts/audit history without touching pending operations.
 responses=queue/'responses';history=queue/'history'
 entries=sorted(responses.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True) if responses.exists() else []
 for i,old in enumerate(entries):
  if i>=1000 or time.time()-old.stat().st_mtime>30*86400:
   (history/(old.stem+'.request.json')).unlink(missing_ok=True);old.unlink(missing_ok=True)
 request=dict(domain=domain,action=action,params=params,evidence=evidence,job_id=context.get('job_id',''))
 if request_id:identifier=str(uuid.UUID(request_id))
 elif action in MUTATIONS:identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps({k:request[k] for k in ['domain','action','params','job_id']},sort_keys=True,ensure_ascii=False)))
 else:identifier=str(uuid.uuid4())
 request['created']=time.time();request['request_id']=identifier
 response=queue/'responses'/(identifier+'.json');file=queue/(identifier+'.request.json')
 if not response.exists():
  if domain=='notes' and action in {'update','delete'}:
   current=call('notes','get',{'id':params['id']},workspace=workspace,timeout=timeout)
   if not current.get('ok'):return current
   if current['data'].get('in_recently_deleted'):raise ValueError('Note is already in Recently Deleted; use Notes to restore it. No second or permanent deletion was performed.')
  encoded=json.dumps(request,ensure_ascii=False)
  if len(encoded.encode())>65536:raise ValueError('Request exceeds native 64KiB limit')
  temp=file.with_suffix('.tmp');temp.write_text(encoded);temp.chmod(0o600);temp.replace(file)
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  if response.exists():
   value=json.loads(response.read_text())
   if value.get('request_id')!=identifier:raise ValueError('Response identity mismatch')
   if domain=='notes':value=_filter_note_response(value,action,params,workspace,timeout)
   return value
  time.sleep(.15)
 return dict(ok=False,request_id=identifier,error='Tool timed out; outcome may be pending. Reuse this request id to check the result; do not create another operation.')

def main():
 parser=argparse.ArgumentParser(description='Local native Calendar / Reminders / Notes tools')
 parser.add_argument('domain',choices=ACTIONS);parser.add_argument('action');parser.add_argument('--params',default='{}',help='JSON object; datetime values require an explicit timezone offset')
 parser.add_argument('--evidence',default='',help='Exact quote from this turn authorizing a write');parser.add_argument('--request-id',help='Full UUID for checking a pending operation; normally omit and use automatic idempotency')
 args=parser.parse_args()
 try:value=call(args.domain,args.action,json.loads(args.params),args.evidence,request_id=args.request_id)
 except Exception as exc:value=dict(ok=False,error=str(exc))
 print(json.dumps(value,ensure_ascii=False))

if __name__=='__main__':main()
