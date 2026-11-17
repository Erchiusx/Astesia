"""One task-scoped speak(text) MCP tool. JSON-lines stdio; no TTS in this process."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid
from memory_mcp import TOOLS as MEMORY_TOOLS,call as memory_call
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from urllib.parse import urlparse

TOOL=dict(name='speak',description='将这段话加入用户的语音播放队列，立即返回；你可以继续调用其他工具或思考。排队成功不表示已播放。',inputSchema=dict(type='object',properties={'text':{'type':'string','minLength':1,'maxLength':5000}},required=['text'],additionalProperties=False),annotations=dict(readOnlyHint=False,destructiveHint=False,idempotentHint=False,openWorldHint=True))

HISTORY_TOOL=dict(name='conversation_search',description='按需检索当前会话的过往对话，最多返回五条。不是长期记忆；query 为空时查最近几条。',inputSchema=dict(type='object',properties={'query':{'type':'string','maxLength':500}},required=['query'],additionalProperties=False),annotations=dict(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=True))

class SpeechTool:
    def __init__(self,context):self.path=Path(context);self.receipts={}

    def call(self,arguments,call_id):
        if not isinstance(arguments,dict) or set(arguments)!={'text'} or not isinstance(arguments['text'],str) or not 1<=len(arguments['text'].strip())<=5000:
            raise ValueError('speak expects only a nonempty text string, at most 5000 characters')
        fingerprint=json.dumps(arguments,sort_keys=True);key=(str(call_id),fingerprint)
        if key in self.receipts:return self.receipts[key]
        context=json.loads(self.path.read_text())
        if context['expires']<=time.time():raise ValueError('This task has expired')
        config=json.loads(Path(context['config_path']).read_text())
        endpoint=config['base_url']+'/worker/speak';url=urlparse(endpoint)
        if url.scheme!='https' and not(url.scheme=='http' and url.hostname in {'localhost','127.0.0.1','::1'}):raise ValueError('HTTPS broker required')
        payload=dict(job_id=context['job_id'],lease_token=context['lease_token'],text=arguments['text'],request_id=str(uuid.uuid4()))
        request=Request(endpoint,data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+config['worker_token'],'Content-Type':'application/json'})
        for attempt in range(2):
            try:
                with urlopen(request,timeout=5) as response:result=json.load(response)
                if not result.get('queued'):raise ValueError('Speech was not queued')
                self.receipts[key]=dict(content=[dict(type='text',text='queued')]);return self.receipts[key]
            except HTTPError as exc:
                raise ValueError('Speech request rejected: HTTP '+str(exc.code)) from None
            except (URLError,TimeoutError,OSError):
                if attempt:raise ValueError('Could not confirm speech queue submission') from None
                time.sleep(.1)

    def dispatch(self,message):
        method=message.get('method');params=message.get('params') or {};identifier=message.get('id')
        if identifier is None:return None
        try:
            if method=='initialize':
                result=dict(protocolVersion=params.get('protocolVersion','2024-11-05'),capabilities={'tools':{}},serverInfo=dict(name='personal-agent-voice',version='1.0'),instructions='speak 只负责将 text 加入语音队列。返回 queued 后可以立即继续工作，不需要等待播放。不调用 speak 就不会朗读你的普通消息；不要再生成语音响应 JSON。')
            elif method=='ping':result={}
            elif method=='tools/list':
                context=json.loads(self.path.read_text());config=json.loads(Path(context['config_path']).read_text())
                result=dict(tools=[TOOL,HISTORY_TOOL]+(MEMORY_TOOLS if config.get('memory_authority')=='mac' else []))
            elif method=='tools/call':
                try:
                    if params.get('name')=='speak':result=self.call(params.get('arguments',{}),identifier)
                    elif params.get('name')=='conversation_search':
                        arguments=params.get('arguments',{})
                        if set(arguments)!={'query'} or not isinstance(arguments['query'],str) or len(arguments['query'])>500:raise ValueError('Invalid query')
                        context=json.loads(self.path.read_text());config=json.loads(Path(context['config_path']).read_text())
                        payload=dict(job_id=context['job_id'],lease_token=context['lease_token'],query=arguments['query'])
                        request=Request(config['base_url']+'/worker/history/search',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+config['worker_token'],'Content-Type':'application/json'})
                        with urlopen(request,timeout=5) as response:value=json.load(response)
                        result=dict(content=[dict(type='text',text=json.dumps(value,ensure_ascii=False))])
                    else:
                        context=json.loads(self.path.read_text());config=json.loads(Path(context['config_path']).read_text())
                        value=memory_call(params.get('name'),params.get('arguments',{}),context,config)
                        result=dict(content=[dict(type='text',text=json.dumps(value,ensure_ascii=False))])
                except (ValueError,OSError,KeyError):result=dict(isError=True,content=[dict(type='text',text='Tool request failed; no success was confirmed. Check arguments and current task context.')])
            else:return dict(jsonrpc='2.0',id=identifier,error=dict(code=-32601,message='Method not found'))
            return dict(jsonrpc='2.0',id=identifier,result=result)
        except (ValueError,KeyError,TypeError):return dict(jsonrpc='2.0',id=identifier,error=dict(code=-32602,message='Invalid parameters'))

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--context',required=True);args=parser.parse_args();tool=SpeechTool(args.context)
    for line in sys.stdin:
        try:
            message=json.loads(line)
            if not isinstance(message,dict):raise ValueError()
            response=tool.dispatch(message)
        except ValueError:response=dict(jsonrpc='2.0',id=None,error=dict(code=-32700,message='Invalid JSON'))
        if response is not None:print(json.dumps(response,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
