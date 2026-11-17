"""Small memory tools over the existing local versioned database.
Task evidence, current versions and receipt IDs are supplied by the adapter.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import time
from local_memory import LocalMemory

def tool(name,description,properties,required,readonly=False):
    return dict(name=name,description=description,inputSchema=dict(type='object',properties=properties,required=required,additionalProperties=False),annotations=dict(readOnlyHint=readonly,destructiveHint=False,idempotentHint=readonly,openWorldHint=False))
STRING=dict(type='string',minLength=1,maxLength=2000)
TOOLS=[
 tool('memory_search','按关键词查询用户长期记忆。不会自动加载全部记忆或聊天历史。',{'query':STRING},['query'],True),
 tool('memory_read','读取一条长期记忆及其依据。',{'key':STRING},['key'],True),
 tool('memory_upsert','保存或更新一条有用户原话依据的稳定事实；key 沿用同一事实，evidence 必须逐字引用本次用户请求。版本和写入由工具维护。',{'key':STRING,'text':STRING,'evidence':STRING,'category':dict(type='string',enum=['profile','preference','interaction','ongoing'])},['key','text','evidence']),
 tool('memory_forget','根据用户本次明确要求遗忘一条记忆。evidence 必须逐字引用本次请求。',{'key':STRING,'evidence':STRING},['key','evidence'])]

def call(name,arguments,context,config):
    spec=next((t for t in TOOLS if t['name']==name),None)
    if spec is None or not isinstance(arguments,dict):raise ValueError('Invalid memory tool')
    schema=spec['inputSchema']
    if set(arguments)-set(schema['properties']) or not set(schema['required'])<=set(arguments):raise ValueError('Invalid memory arguments')
    for key,value in arguments.items():
        if not isinstance(value,str) or not 1<=len(value)<=2000:raise ValueError('Invalid memory argument')
    readonly=name in ('memory_search','memory_read')
    if context['expires']<=time.time() or time.time()-context.get('created',0)>900:raise ValueError('Memory task expired')
    if not readonly and context['kind']!='ask':raise ValueError('Background memory writes are disabled')
    if config.get('memory_authority')!='mac':raise ValueError('Local memory is not enabled')
    root=Path(config['state_dir'])/'workspace/memory';memory=LocalMemory(root,readonly=readonly)
    try:
        if name=='memory_search':return memory.search(arguments['query'])
        if name=='memory_read':return memory.read([arguments['key']])
        records=memory.read([arguments['key']]);old=records[0] if records else None
        category=arguments.get('category') or (old['category'] if old else arguments['key'].split('.')[0])
        if category not in ('profile','preference','interaction','ongoing'):category='preference'
        change=dict(operation='delete' if name=='memory_forget' else 'upsert',key=arguments['key'],category=category,text=arguments.get('text',''),evidence=arguments['evidence'],evidence_source='user',evidence_key='',version=old['version'] if old else 0,expires_at=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=30)).isoformat() if category=='ongoing' else '',confidence=1.0,reason='Current user statement')
        receipt='mcp:'+hashlib.sha256((context['job_id']+name+json.dumps(arguments,sort_keys=True)).encode()).hexdigest()
        return memory.apply([change],dict(job_id=context['job_id'],kind=context['kind'],question=context['question']),receipt=receipt)
    finally:memory.close()
