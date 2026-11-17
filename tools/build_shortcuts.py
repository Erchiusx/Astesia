"""Private read-only Health export and sleep-event signal workflows (iPhone)."""
import argparse,json,pathlib,plistlib,uuid,subprocess,os
parser=argparse.ArgumentParser(description='Generate private iPhone workflows; never reads the installed Shortcuts database.')
parser.add_argument('--setup',required=True,type=pathlib.Path,help='Private client-setup.json with url and headers')
parser.add_argument('--output',required=True,type=pathlib.Path)
parser.add_argument('--unsigned',action='store_true',help='For static inspection; iPhone import generally requires signing on macOS')
args=parser.parse_args();os.umask(0o077)
setup=json.loads(args.setup.expanduser().read_text());BASE=setup['url'].rsplit('/',1)[0]
OUT=args.output.expanduser().resolve();BUILD=OUT/'unsigned';OUT.mkdir(parents=True,exist_ok=True,mode=0o700);BUILD.mkdir(exist_ok=True,mode=0o700)
repo=pathlib.Path(__file__).resolve().parents[1]
if OUT==repo or repo in OUT.parents:raise SystemExit('Generated files contain credentials; choose an output directory outside the repository')
def uid():return str(uuid.uuid4()).upper()
def action(name,**p):return dict(WFWorkflowActionIdentifier='is.workflow.actions.'+name,WFWorkflowActionParameters=p)
def token(v):return dict(WFSerializationType='WFTextTokenAttachment',Value=v)
def ref(u,name='Result'):return token(dict(Type='ActionOutput',OutputUUID=u,OutputName=name))
def variable(name):return token(dict(Type='Variable',VariableName=name))
def text(s):return dict(WFSerializationType='WFTextTokenString',Value={'string':s,'attachmentsByRange':{}})
def rich(v):return dict(WFSerializationType='WFTextTokenString',Value={'string':'\ufffc','attachmentsByRange':{'{0, 1}':v['Value']}})
def dictionary(values):return dict(WFSerializationType='WFDictionaryFieldValue',Value={'WFDictionaryFieldValueItems':[dict(WFItemType=0,WFKey=text(k),WFValue=rich(v) if isinstance(v,dict) else text(str(v))) for k,v in values.items()]})
def post(path,body):
 u=uid();return [action('url',UUID=u,WFURLActionURL=BASE+path),action('downloadurl',UUID=uid(),WFInput=ref(u,'URL'),WFURL=rich(ref(u,'URL')),WFHTTPMethod='POST',WFHTTPHeaders=dictionary(setup['headers']),WFHTTPBodyType='JSON',WFJSONValues=dictionary(body))]
def date_format(source):
 u=uid();return u,action('format.date',UUID=u,WFDate=rich(source),WFDateFormatStyle='ISO 8601',WFISO8601IncludeTime=True)
def save(name,actions):
 known=set();stack=[]
 def validate(x):
  if isinstance(x,dict):
   if x.get('Type')=='ActionOutput':assert x['OutputUUID'] in known,x
   for v in x.values():validate(v)
  elif isinstance(x,list):
   for v in x:validate(v)
 for a in actions:
  p=a['WFWorkflowActionParameters'];validate(p)
  if 'WFControlFlowMode' in p:
   if p['WFControlFlowMode']==0:stack.append(p['GroupingIdentifier'])
   elif p['WFControlFlowMode']==2:assert stack.pop()==p['GroupingIdentifier']
   else:assert stack[-1]==p['GroupingIdentifier']
  if p.get('UUID'):known.add(p['UUID'])
 assert not stack
 obj=dict(WFWorkflowName=name,WFWorkflowIdentifier=str(uuid.uuid5(uuid.NAMESPACE_URL,'org.personalagent.oss.'+name)).upper(),WFWorkflowClientVersion='3036.0.4.2',WFWorkflowMinimumClientVersion=900,WFWorkflowMinimumClientVersionString='900',WFWorkflowIcon=dict(WFWorkflowIconStartColor=4282601983,WFWorkflowIconGlyphNumber=59511),WFWorkflowTypes=[],WFWorkflowInputContentItemClasses=['WFStringContentItem'],WFWorkflowActions=actions,WFWorkflowHasOutputFallback=False,WFWorkflowImportQuestions=[],WFWorkflowNoInputBehavior={'Name':'WFWorkflowNoInputBehaviorContinue','Parameters':{}})
 p=BUILD/(name+'.unsigned.shortcut');p.write_bytes(plistlib.dumps(obj,fmt=plistlib.FMT_BINARY));p.chmod(0o600);signed=OUT/(name+'.shortcut')
 if not args.unsigned:
  subprocess.run(['/usr/bin/shortcuts','sign','--mode','anyone','--input',str(p),'--output',str(signed)],check=True);signed.chmod(0o600)
 print(name,len(actions),'actions; statically validated',flush=True)


signal=[action('comment',WFCommentActionText='睡眠自动化专用。输入 wake：登记起床30分钟后的简报；输入 wind_down：按已配置助眠时长登记睡前5分钟简报。手动无输入只检测连接，不登记。不会读取健康数据，也不在手机后台等待。文件含私人接口令牌，不要分享。')]
signal+=post('/routine/signal',{'event':token({'Type':'ExtensionInput'})})
save('助手·睡眠计划信号',signal)

kinds=[('sleep','Sleep',200,2),('steps','Steps',1000,2),('active_calories','Active Calories',500,2),('exercise_minutes','Exercise Minutes',500,2),('heart_rate','Heart Rate',50,2),('resting_heart_rate','Resting Heart Rate',7,7)]
actions=[action('comment',WFCommentActionText='仅在 iPhone 上运行。只读睡眠、步数、活动热量、锻炼分钟、心率、静息心率；不写入健康。把最近有限样本发到你自己的 broker 接口，再由 Mac 助手保存和按需读取。首次请解锁手机，允许相应健康读取和联网。锁屏读不到时不要当作零；早晚信号独立运行。文件含私人令牌，不要分享。')]
capture,fmt=date_format(token({'Type':'CurrentDate'}));actions.append(fmt);body=dict(schema_version='1',captured_at=ref(capture,'Formatted Date'))
for metric,label,limit,days in kinds:
 find=uid();group=uid()
 rows=[dict(Bounded=True,Operator=4,Property='Type',Removable=False,Values={'Enumeration':{'WFSerializationType':'WFStringSubstitutableState','Value':label}}),dict(Bounded=True,Operator=1001,Property='Start Date',Removable=True,Values={'Number':str(days),'Unit':16384})]
 filters=dict(WFSerializationType='WFContentPredicateTableTemplate',Value={'WFActionParameterFilterPrefix':1,'WFContentPredicateBoundedDate':False,'WFActionParameterFilterTemplates':rows})
 actions.append(action('filter.health.quantity',UUID=find,WFContentItemFilter=filters,WFContentItemLimitEnabled=True,WFContentItemLimitNumber=limit,WFContentItemSortProperty='Start Date',WFContentItemSortOrder='Latest First'))
 actions.append(action('conditional',WFInput={'Type':'Variable','Variable':ref(find,'Health Samples')},WFCondition=100,GroupingIdentifier=group,WFControlFlowMode=0))
 for field,prop in [('values','Value'),('starts','Start Date'),('ends','End Date'),('sources','Source'),('units','Unit')]:
  if metric=='sleep' and field=='units':
   value=uid();actions.append(action('gettext',UUID=value,WFTextActionText='category'));output=ref(value,'Text')
  else:
   value=uid();actions.append(action('properties.health.quantity',UUID=value,WFContentItemPropertyName=prop,WFInput=ref(find,'Health Samples')));output=ref(value,prop)
   if field in ['starts','ends']:
    value,fmt=date_format(output);actions.append(fmt);output=ref(value,'Formatted Date')
  key=metric+'_'+field;actions.append(action('setvariable',WFVariableName=key,WFInput=output));body[key]=variable(key)
 actions.append(action('conditional',GroupingIdentifier=group,WFControlFlowMode=1));empty=uid();actions.append(action('gettext',UUID=empty,WFTextActionText=''))
 for field in ['values','starts','ends','sources','units']:actions.append(action('setvariable',WFVariableName=metric+'_'+field,WFInput=ref(empty,'Text')))
 actions.append(action('conditional',GroupingIdentifier=group,WFControlFlowMode=2))
actions+=post('/wellness/ingest',body)
save('助手·同步健康摘要',actions)

# Manual stop, edit/cancel, then one upload. No polling or local speech reply.
dictation=uid();review=uid();group=uid()
question=[action('dictatetext',UUID=dictation,WFDictateTextLanguage='zh-CN',WFSpeechLanguage='zh-CN',WFDictateTextStopListening='On Tap'),
 action('ask',UUID=review,WFInputType='Text',WFAskActionPrompt='检查听写；完成后发送，取消则放弃。',WFAskActionDefaultAnswer=rich(ref(dictation,'Dictated Text'))),
 action('conditional',WFInput={'Type':'Variable','Variable':ref(review,'Provided Input')},WFCondition=100,GroupingIdentifier=group,WFControlFlowMode=0)]
question+=post('/ask',{'text':ref(review,'Provided Input')})
question.append(action('conditional',GroupingIdentifier=group,WFControlFlowMode=2))
save('问生活助手',question)
