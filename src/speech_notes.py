"""Independent speech-to-Notes collector; never blocks speak(), TTS or the agent."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import signal
import threading
from urllib.request import Request,urlopen
from zoneinfo import ZoneInfo


def snapshot(entries,keep=3,timezone='UTC'):
    if type(keep)!=int or not 1<=keep<=3:raise ValueError('Keep must be 1 to 3')
    if not isinstance(entries,list):raise ValueError('Invalid entries')
    unique={}
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get('id'),str) or not 1<=len(entry['id'])<=100:raise ValueError('Invalid entry id')
        if not isinstance(entry.get('text'),str) or not 1<=len(entry['text'])<=5000:raise ValueError('Invalid speech text')
        if type(entry.get('created')) not in (int,float) or entry['created']<0:raise ValueError('Invalid speech time')
        unique[entry['id']]={k:entry[k] for k in ('id','text','created')}
    selected=sorted(unique.values(),key=lambda e:(e['created'],e['id']))[-keep:]
    zone=ZoneInfo(timezone)
    body='\n\n'.join(dt.datetime.fromtimestamp(e['created'],zone).strftime('%Y-%m-%d %H:%M')+'\n'+e['text'] for e in reversed(selected))
    if not selected:body='后续语音会同步到这里，仅保留最近 '+str(keep)+' 条。'
    version=hashlib.sha256(json.dumps(dict(entries=selected,keep=keep,timezone=timezone),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return dict(version=version,entries=selected,body=body)


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=path.with_suffix('.tmp')
    with tmp.open('w') as out:json.dump(value,out,ensure_ascii=False);out.flush();os.fsync(out.fileno())
    tmp.chmod(0o600);tmp.replace(path)


class Collector:
    def __init__(self,config,root):self.config=config;self.root=Path(root)
    def tick(self):
        policy=json.loads((self.root/'config.json').read_text())
        if not policy.get('enabled'):return
        request=Request(self.config['base_url']+'/worker/speech-notes/recent',data=json.dumps(dict(after=policy.get('after',0))).encode(),headers={'Authorization':'Bearer '+self.config['worker_token'],'Content-Type':'application/json'})
        with urlopen(request,timeout=10) as response:entries=json.load(response)['entries']
        value=snapshot(entries,policy.get('keep',3),policy.get('timezone','UTC'));value['mirror_id']=policy['mirror_id']
        target=self.root/'desired.json'
        try:old=json.loads(target.read_text())
        except (ValueError,OSError):old={}
        if old.get('version')!=value['version'] or old.get('mirror_id')!=value['mirror_id']:save(target,value)


def main():
    os.umask(0o077);p=argparse.ArgumentParser();p.add_argument('--config',required=True,type=Path);p.add_argument('--root',required=True,type=Path);args=p.parse_args()
    config=json.loads(args.config.expanduser().read_text());stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    collector=Collector(config,args.root.expanduser())
    while not stop.is_set():
        try:collector.tick()
        except Exception as exc:print('speech_notes_retry',type(exc).__name__,flush=True)
        stop.wait(4)

if __name__=='__main__':main()
