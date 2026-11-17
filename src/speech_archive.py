"""Private, durable user-authorized PTT dataset. Auto transcripts are not labels."""
import datetime,json,os,shutil,tempfile
from pathlib import Path


class SpeechArchive:
    def __init__(self,config):
        self.root=Path(config['root']);self.volume=config['required_volume'];self.since=config['started_at']

    def save(self,row,**updates):
        if row['created']<self.since:return
        if not os.path.ismount(self.volume):raise OSError('Recording archive volume is not mounted')
        day=datetime.datetime.fromtimestamp(row['created'],datetime.timezone.utc).strftime('%Y-%m-%d')
        folder=self.root/day/row['id'];folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        source=Path(row['audio']);target=folder/'original.opus'
        if not target.exists():
            if not source.exists():raise OSError('Recording source is missing')
            self.copy(source,target)
        wav=source.with_suffix('.wav')
        if wav.exists() and not (folder/'decoded.wav').exists():self.copy(wav,folder/'decoded.wav')
        file=folder/'record.json'
        value=json.loads(file.read_text()) if file.exists() else dict(id=row['id'],created=row['created'],audio='original.opus',auto_text=None,corrected_text=None,label_status='unreviewed',engine='qwen3_asr',consent='user_requested_ptt_retention')
        if row.get('text') is not None:value['auto_text']=row['text']
        value.update(updates)
        fd,name=tempfile.mkstemp(prefix='.record-',dir=folder)
        try:
            with os.fdopen(fd,'w') as out:json.dump(value,out,ensure_ascii=False,indent=2);out.flush();os.fsync(out.fileno())
            os.replace(name,file)
        finally:
            if os.path.exists(name):os.unlink(name)

    @staticmethod
    def copy(source,target):
        fd,name=tempfile.mkstemp(prefix='.audio-',dir=target.parent)
        try:
            with os.fdopen(fd,'wb') as out,source.open('rb') as inp:
                shutil.copyfileobj(inp,out);out.flush();os.fsync(out.fileno())
            os.replace(name,target)
        finally:
            if os.path.exists(name):os.unlink(name)
