from runtime_paths import CONFIG_DIR, DATA_DIR
"""Candidate duplex voice entry point. Replaces, never runs beside, old delivery."""
import fcntl
import json
import os
from pathlib import Path
import queue
import select
import shutil
import signal
import subprocess
import threading
import time
import uuid
from voice_core import VoiceDelivery,ROOT
from voice_inbox import VoiceInbox
from speech_archive import SpeechArchive


class Transport:
    def __init__(self,config,inbox,receiver,feedback=None,notify=None):
        self.config=config;self.inbox=inbox;self.receiver=receiver;self.feedback=feedback;self.notify=notify
        self.process=None;self.lock=threading.RLock();self.pending={};self.running=True

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:return
            if self.process:
                self.process.wait();self.process.stdin.close();self.process.stdout.close()
                for item in self.pending.values():
                    item['result']={'ok':False};item['event'].set()
            self.process=subprocess.Popen([self.config['node'],str(ROOT/'zello_bridge.mjs'),str(CONFIG_DIR/'zello-config.json'),str(self.inbox.root)]+(['--native-stream'] if self.config.get('voice_input_engine')=='codex_webrtc' else []),
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
            threading.Thread(target=self.read,args=(self.process,),daemon=True).start()

    def read(self,process):
        try:
            for line in process.stdout:
                try:
                    value=json.loads(line)
                    if value.get('event')=='incoming' or (value.get('event')=='incoming_start' and self.config.get('voice_input_engine')=='codex_webrtc'):
                        self.inbox.register(value['metadata'],self.receiver)
                        if self.notify:self.notify()
                    elif 'id' in value:
                        with self.lock:
                            item=self.pending.get(value['id'])
                            if item:item['result']=value;item['event'].set()
                    elif value.get('event')=='connection':print('zello_connection',bool(value.get('online')),flush=True)
                    elif value.get('event')=='input_dropped':
                        print('voice_input_dropped',value.get('reason','unknown'),flush=True)
                        if self.feedback and (self.config.get('voice_input_engine')!='codex_webrtc' or value.get('reason')=='spool_failure'):
                            self.feedback('刚才的语音没有完整收到，请再按住通话键说一次。')
                except Exception as exc:print('voice_input_event_error',type(exc).__name__,flush=True)
        finally:
            with self.lock:
                if self.process is process:
                    for item in self.pending.values():
                        item['result']={'ok':False};item['event'].set()

    def send(self,file,timeout):
        self.start();key=uuid.uuid4().hex;item={'event':threading.Event(),'result':None}
        try:
            with self.lock:
                self.pending[key]=item
                self.process.stdin.write((json.dumps(dict(command='send',id=key,file=str(file)))+'\n').encode())
                self.process.stdin.flush()
            if not item['event'].wait(timeout):
                self.kill();raise TimeoutError('Voice transport timeout')
            if not item['result'] or not item['result'].get('ok'):raise RuntimeError('Voice transmission failed')
        finally:
            with self.lock:self.pending.pop(key,None)

    def kill(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                try:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass


class Recognizer:
    def __init__(self,config):self.config=config;self.process=None;self.buffer=b''

    def response(self,seconds):
        deadline=time.monotonic()+seconds
        while b'\n' not in self.buffer:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('ASR timeout')
            if select.select([self.process.stdout],[],[],min(remaining,1))[0]:
                chunk=os.read(self.process.stdout.fileno(),65536)
                if not chunk:raise RuntimeError('ASR process stopped')
                self.buffer+=chunk
        line,self.buffer=self.buffer.split(b'\n',1);return json.loads(line)

    def prepare(self):
        if not self.process or self.process.poll() is not None:
            self.stop()
            self.process=subprocess.Popen([self.config['voice_asr_python'],'-u',str(ROOT/('qwen_speech_to_text.py' if self.config.get('voice_input_engine')=='qwen3_asr' else 'speech_to_text.py')),str(CONFIG_DIR/'worker-config.json')],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
            if not self.response(120).get('ready'):raise RuntimeError('ASR not ready')

    def transcribe(self,audio):
        self.prepare()
        self.process.stdin.write((json.dumps({'audio':str(audio)})+'\n').encode());self.process.stdin.flush()
        value=self.response(180)
        if not value.get('ok'):raise RuntimeError('ASR failed')
        return value

    def stop(self):
        if self.process:
            if self.process.poll() is None:
                try:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            self.process.wait();self.process.stdin.close();self.process.stdout.close()
        self.process=None;self.buffer=b''


class DuplexDelivery(VoiceDelivery):
    def __init__(self,config):
        super().__init__(config)
        secret=json.loads((CONFIG_DIR/'zello-config.json').read_text());receiver=secret.get('receiver','')
        if not receiver or receiver.casefold()==secret['username'].casefold():raise ValueError('Receiver account required')
        self.inbox=VoiceInbox(DATA_DIR/'voice-inbox.sqlite3',config['voice_inbox_dir'],
            SpeechArchive(config['speech_archive']) if config.get('speech_archive',{}).get('enabled') else None)
        self.feedback_queue=queue.Queue(maxsize=3);self.last_feedback=float('-inf')
        self.input_ready=threading.Event()
        self.transport=Transport(config,self.inbox,receiver,self.feedback,self.input_ready.set);self.recognizer=Recognizer(config)
        self.receiver=receiver
        for metadata in self.inbox.root.glob('*.json'):
            try:self.inbox.register(metadata,receiver)
            except (ValueError,OSError,KeyError):pass
        self.command_lock=threading.RLock()

    def feedback(self,text):
        if time.monotonic()-self.last_feedback<10:return
        self.last_feedback=time.monotonic()
        try:self.feedback_queue.put_nowait(dict(id='local-feedback-'+uuid.uuid4().hex,
            spoken_text=text,lease_token='local',job_id=None))
        except queue.Full:pass

    def request(self,path,payload):
        # Recognition notices use the same serialized TTS/sender, never a model job.
        if path=='/worker/messages/claim':
            try:return {'message':self.feedback_queue.get_nowait()}
            except queue.Empty:pass
        if path in ('/worker/messages/heartbeat','/worker/messages/complete') and str(payload.get('message_id','')).startswith('local-feedback-'):
            return {'ok':True}
        return super().request(path,payload)

    def command(self,args,timeout):
        if Path(args[0]).name=='node' and Path(args[1]).name=='zello_sender.mjs':
            return self.transport.send(args[3],max(timeout,240))
        with self.command_lock:return super().command(args,timeout)

    def ingest(self):
        try:
            started=time.monotonic()
            if self.config.get('voice_input_engine')!='codex_webrtc':self.recognizer.prepare()
            print('voice_input_ready codex_webrtc' if self.config.get('voice_input_engine')=='codex_webrtc' else 'voice_asr_ready '+str(round(time.monotonic()-started,2)),flush=True)
        except Exception as exc:print('voice_asr_warmup_retry',type(exc).__name__,flush=True)
        while self.running:
            row=None
            try:
                self.transport.start();self.inbox.cleanup();row=self.inbox.next()
                if not row:
                    self.input_ready.wait(2);self.input_ready.clear();continue
                if self.config.get('voice_input_engine')=='codex_webrtc':
                    result=self.request('/worker/audio/ask',dict(source_id=row['id']))
                    # Keep the packet journal until the model worker has completed.
                    with self.inbox.lock,self.inbox.db:
                        self.inbox.db.execute("UPDATE incoming SET status='native_queued',job_id=?,updated=? WHERE id=? AND status NOT IN ('submitted','failed')",(result['id'],time.time(),row['id']))
                    print('native_voice_queued',row['id'],result['id'],flush=True)
                    continue
                if self.inbox.archive:self.inbox.archive_record(row,status='received')
                if not row['text']:
                    audio=Path(row['audio']);wav=audio.with_suffix('.wav')
                    # Opus packets are decoded rather than played into a virtual microphone.
                    self.command([self.config['opusdec'],'--quiet',str(audio),str(wav)],30)
                    started=time.monotonic();value=self.recognizer.transcribe(wav)
                    print('voice_transcribed_seconds',round(time.monotonic()-started,2),flush=True)
                    text=value['text'].strip()
                    if value.get('needs_review') or not self.config.get('voice_auto_submit',True):
                        self.inbox.update(row['id'],status='needs_review',text=text,error=None)
                        if self.inbox.archive:self.inbox.archive_record(dict(row,text=text),status='needs_review')
                        self.feedback('这段话我没有听清，请再按住通话键说一次。')
                        continue
                    self.inbox.update(row['id'],status='transcribed',text=text,error=None)
                    row['text']=text
                    if self.inbox.archive:self.inbox.archive_record(row,status='transcribed')
                result=self.request('/worker/ask',dict(text=row['text'],source_id=row['id'],
                    metadata=dict(sender=self.receiver,language='zh',recognition=self.config.get('voice_input_engine','automatic'))))
                self.inbox.update(row['id'],status='submitted',job_id=result['id'],error=None)
                if self.inbox.archive:self.inbox.archive_record(row,status='submitted',job_id=result['id'])
                try:self.inbox.remove_audio(row)
                except OSError:print('voice_audio_retained_for_archive',row['id'],flush=True)
                print('voice_input_submitted',row['id'],result['id'],'elapsed',round(time.time()-row['created'],2),flush=True)
            except Exception as exc:
                if not self.running:return
                print('voice_input_retry',type(exc).__name__,flush=True)
                if 'row' in locals() and row:
                    if row.get('text') or self.config.get('voice_input_engine')=='codex_webrtc':
                        count=row['attempts']+1
                        self.inbox.update(row['id'],status='needs_review' if count>=8 else ('new' if self.config.get('voice_input_engine')=='codex_webrtc' else 'transcribed'),
                            attempts=count,retry_at=time.time()+min(2**count*3,90),error=type(exc).__name__)
                    else:
                        self.recognizer.stop();self.inbox.update(row['id'],status='failed',error=type(exc).__name__)
                        self.feedback('刚才的语音暂时没能识别，请再按住通话键说一次。')
                time.sleep(3)

    def run(self):
        self.transport.start()
        threading.Thread(target=self.ingest,daemon=True).start()
        try:super().run()
        finally:self.transport.kill();self.recognizer.stop()

    def stop(self,*args):
        super().stop(*args);self.transport.running=False;self.transport.kill();self.recognizer.stop()


def main():
    os.umask(0o077)
    config=json.loads((CONFIG_DIR/'worker-config.json').read_text())
    if config.get('voice_input_engine')!='qwen3_asr':raise SystemExit('This release supports qwen3_asr voice input only')
    if not config.get('zello_duplex_enabled',False):raise SystemExit('Duplex voice is not enabled')
    config.update(node=shutil.which('node'),opusenc=shutil.which('opusenc'),opusdec=shutil.which('opusdec'))
    required=('node','opusenc','opusdec','voice_inbox_dir')
    if config.get('voice_input_engine')=='codex_webrtc':
        required+=('native_voice_python','codex_home')
    else:required+=('voice_asr_python','voice_asr_model_dir')
    if not all(config.get(key) for key in required):raise SystemExit('Missing configured voice-input dependencies')
    if config.get('voice_input_engine')!='codex_webrtc' and not Path(config['voice_asr_model_dir']).expanduser().is_dir():raise SystemExit('Local ASR model missing')
    (DATA_DIR/'voice-work').mkdir(mode=0o700,exist_ok=True)
    with (DATA_DIR/'voice-delivery.lock').open('w') as lock:
        # The existing production sender owns this lock until an approved cutover.
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('A voice worker is already running; refusing a second Zello login')
        delivery=DuplexDelivery(config)
        signal.signal(signal.SIGTERM,delivery.stop);signal.signal(signal.SIGINT,delivery.stop)
        delivery.run()


if __name__=='__main__':main()
