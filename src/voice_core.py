from runtime_paths import CONFIG_DIR, DATA_DIR
"""Outbound Mac voice-delivery worker, independent of model execution."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import select
import signal
import subprocess
import tempfile
import time
import threading
import wave
from urllib.error import HTTPError
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parent


class VoiceExpired(Exception):pass


class VoiceDelivery:
    def __init__(self,config):
        self.config=config
        self.running=True
        self.child=None
        self.tts_child=None
        self.tts_buffer=b''

    def request(self,path,payload):
        req=Request(self.config['base_url']+path,data=json.dumps(payload,ensure_ascii=False).encode(),
            headers={'Authorization':'Bearer '+self.config['worker_token'],'Content-Type':'application/json'})
        with urlopen(req,timeout=20) as r:return json.load(r)

    def command(self,args,timeout):
        process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        self.child=process
        try:
            stdout,stderr=process.communicate(timeout=timeout)
            if process.returncode:
                # Log only known diagnostics, never credential contents or arguments.
                if Path(args[0]).name=='node':
                    print('zello_sender_failed',stderr.decode(errors='replace')[-250:].strip(),flush=True)
                raise RuntimeError('Voice delivery command failed')
        except subprocess.TimeoutExpired:
            self.kill_child();process.communicate()
            raise RuntimeError('Voice delivery timed out')
        finally:self.child=None

    def kill_child(self):
        process=self.child
        if process and process.poll() is None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass

    def close_tts(self):
        process=self.tts_child
        self.tts_child=None;self.tts_buffer=b''
        if process:
            if process.poll() is None:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            process.wait(timeout=5)
            process.stdin.close();process.stdout.close()

    def tts_response(self,timeout):
        deadline=time.monotonic()+timeout
        while b'\n' not in self.tts_buffer:
            if not self.running:raise RuntimeError('Stopping')
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Speech synthesis timed out')
            if select.select([self.tts_child.stdout],[],[],min(remaining,.5))[0]:
                chunk=os.read(self.tts_child.stdout.fileno(),65536)
                if not chunk:raise RuntimeError('Speech process stopped')
                self.tts_buffer+=chunk
        line,self.tts_buffer=self.tts_buffer.split(b'\n',1)
        return json.loads(line)

    def prepare_tts(self):
        if self.tts_child and self.tts_child.poll() is None:return
        self.close_tts()
        self.tts_child=subprocess.Popen([self.config['gpt_python'],'-u',str(ROOT/'gpt_voice_service.py'),
            str(CONFIG_DIR/'worker-config.json')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,start_new_session=True)
        if not self.tts_response(60).get('ready'):raise RuntimeError('Speech model not ready')
        print('voice_model_ready gpt_sovits',flush=True)

    def synthesize(self,text,source,wav):
        if self.config.get('voice_engine')=='gpt_sovits':
            try:
                self.prepare_tts()
                self.tts_child.stdin.write((json.dumps({'text':text,'output':str(wav)},ensure_ascii=False)+'\n').encode())
                self.tts_child.stdin.flush()
                result=self.tts_response(150)
                if not result.get('ok'):raise RuntimeError('Speech synthesis failed')
                print('voice_synthesized gpt_sovits',result['elapsed'],flush=True)
                return
            except Exception as exc:
                self.close_tts()
                if not self.running:raise
                print('voice_fallback',type(exc).__name__,flush=True)
        self.command(['/usr/bin/say','-v',self.config.get('voice','Tingting'),'-r',str(self.config.get('voice_rate',185)),
            '-f',str(source),'-o',str(wav),'--file-format','WAVE','--data-format',
            'LEI16@'+str(self.config.get('voice_sample_rate',48000))],20)

    def process(self,message):
        if message.get('expires',float('inf'))<=time.time():raise VoiceExpired()
        # Long spoken results are shortened before synthesis, then measured.
        # Full reply remains available through the authenticated broker API.
        text=message['spoken_text'].strip()
        if len(text)>450:text=text[:420]+'。完整回答可以在会话页面查看。'
        text=text.removeprefix('提醒。').removeprefix('提醒，')
        greeting=self.config.get('voice_greeting','')
        if message.get('kind')!='speech' and not text.startswith(greeting):text=greeting+text
        with tempfile.TemporaryDirectory(prefix='voice-',dir=DATA_DIR/'voice-work') as folder:
            folder=Path(folder);source=folder/'text.txt';wav=folder/'voice.wav';opus=folder/'voice.opus'
            source.write_text(text)
            for _ in range(3):
                self.synthesize(text,source,wav)
                with wave.open(str(wav)) as audio:seconds=audio.getnframes()/audio.getframerate()
                if seconds<85:break
                text=text[:max(60,int(len(text)*75/seconds))]+'。完整回答可以在会话页面查看。'
                source.write_text(text)
            if seconds>=85:raise RuntimeError('Voice response too long')
            # Keep one private original for "save that reply"; replace, do not accumulate.
            try:
                latest=DATA_DIR/'latest-voice.wav'
                temporary=DATA_DIR/'latest-voice.tmp.wav'
                shutil.copyfile(wav,temporary);temporary.chmod(0o600);temporary.replace(latest)
                metadata=DATA_DIR/'latest-voice.json'
                temporary=metadata.with_suffix('.tmp')
                temporary.write_text(json.dumps(dict(message_id=message['id'],job_id=message.get('job_id'),created=time.time()),ensure_ascii=False))
                temporary.chmod(0o600);temporary.replace(metadata)
            except OSError as exc:print('voice_original_cache',type(exc).__name__,flush=True)
            self.command([self.config['opusenc'],'--quiet','--speech','--downmix-mono','--framesize','20','--bitrate',str(self.config.get('voice_bitrate',64)),str(wav),str(opus)],15)
            if not self.running:return
            if time.time()+seconds+2>message.get('expires',float('inf')):raise VoiceExpired()
            self.command([self.config['node'],str(ROOT/'zello_sender.mjs'),'send-audio',str(opus),str(CONFIG_DIR/'zello-config.json')],145)

    def stop(self,*_):
        self.running=False;self.kill_child();self.close_tts()

    def renew_lease(self,message,finished):
        while not finished.wait(30):
            try:self.request('/worker/messages/heartbeat',dict(message_id=message['id'],lease_token=message['lease_token']))
            except Exception as exc:print('voice_lease_retry',type(exc).__name__,flush=True)

    def run(self):
        if self.config.get('voice_engine')=='gpt_sovits':
            try:self.prepare_tts()
            except Exception as exc:
                self.close_tts();print('voice_model_retry',type(exc).__name__,flush=True)
        while self.running:
            try:
                message=self.request('/worker/messages/claim',{})['message']
                if not message:time.sleep(2);continue
                error=None
                finished=threading.Event()
                heartbeat=threading.Thread(target=self.renew_lease,args=(message,finished),daemon=True)
                heartbeat.start()
                try:
                    try:self.process(message)
                    except VoiceExpired:error='expired'
                    except Exception as exc:error=type(exc).__name__
                finally:finished.set();heartbeat.join(timeout=1)
                if not self.running:return
                payload=dict(message_id=message['id'],lease_token=message['lease_token'],error=error)
                # Retry the receipt rather than repeating already sent speech.
                for attempt in range(6):
                    try:
                        self.request('/worker/messages/complete',payload);break
                    except HTTPError as exc:
                        if exc.code==400:break
                        time.sleep(min(2**attempt,10))
                    except Exception:time.sleep(min(2**attempt,10))
                print('voice_delivery',message['id'],'sent_to_zello' if error is None else error,flush=True)
            except Exception as exc:
                print('voice_retry',type(exc).__name__,flush=True);time.sleep(5)


def main():
    os.umask(0o077)
    config=json.loads((CONFIG_DIR/'worker-config.json').read_text())
    config.update(node=shutil.which('node'),opusenc=shutil.which('opusenc'))
    if not config['node'] or not config['opusenc']:raise SystemExit('Missing audio dependencies')
    (DATA_DIR/'voice-work').mkdir(mode=0o700,exist_ok=True)
    with (DATA_DIR/'voice-delivery.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Voice delivery already running')
        delivery=VoiceDelivery(config)
        signal.signal(signal.SIGTERM,delivery.stop);signal.signal(signal.SIGINT,delivery.stop)
        delivery.run()


if __name__=='__main__':main()
