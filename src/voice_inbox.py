"""Candidate local inbox for durable received audio and transcript review."""
import json
from pathlib import Path
import re
import sqlite3
import threading
import time


class VoiceInbox:
    def __init__(self,path,root,archive=None):
        self.archive=archive
        self.path=Path(path);self.root=Path(root).expanduser().resolve();self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(self.path,check_same_thread=False);self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS incoming (
              id TEXT PRIMARY KEY,status TEXT,metadata_path TEXT,audio TEXT,text TEXT,
              job_id TEXT,error TEXT,created REAL,updated REAL,retry_at REAL DEFAULT 0,
              attempts INTEGER DEFAULT 0);
        ''');self.path.chmod(0o600)
        with self.db:self.db.execute("UPDATE incoming SET status='new' WHERE status='processing'")

    def register(self,metadata_path,receiver):
        file=Path(metadata_path).resolve()
        if self.root not in file.parents or file.suffix!='.json':raise ValueError('Invalid metadata path')
        if file.stat().st_size>8192:raise ValueError('Metadata too large')
        value=json.loads(file.read_text());audio=Path(value['audio']).resolve();key=value['id']
        if not re.fullmatch(r'[a-f0-9-]{36}-[0-9]+',key):raise ValueError('Invalid message id')
        if self.root not in audio.parents or audio.name!=key+'.opus':raise ValueError('Invalid audio path')
        if str(value['sender']).casefold()!=receiver.casefold():raise ValueError('Unexpected sender')
        now=time.time()
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO incoming (id,status,metadata_path,audio,created,updated) VALUES (?,?,?,?,?,?)',
                (key,'new',str(file),str(audio),now,now))
        if self.archive and audio.exists():
            self.archive_record(dict(id=key,created=now,audio=str(audio),text=None),status='received')

    def next(self):
        with self.lock,self.db:
            row=self.db.execute("SELECT * FROM incoming WHERE status IN ('new','transcribed') AND retry_at<=? ORDER BY created LIMIT 1",(time.time(),)).fetchone()
            if not row:return None
            if time.time()-row['created']>900:
                self.db.execute("UPDATE incoming SET status='expired',updated=? WHERE id=?",(time.time(),row['id']))
                return None
            self.db.execute('UPDATE incoming SET status=?,updated=? WHERE id=?',('processing' if not row['text'] else 'transcribed',time.time(),row['id']))
            return dict(row)

    def update(self,key,**values):
        allowed={'status','text','job_id','error','retry_at','attempts'}
        if not values or not set(values)<=allowed:raise ValueError('Invalid inbox update')
        values['updated']=time.time()
        with self.lock,self.db:
            self.db.execute('UPDATE incoming SET '+','.join(name+'=?' for name in values)+' WHERE id=?',list(values.values())+[key])

    def archive_record(self,row,**updates):
        if not self.archive:return True
        try:self.archive.save(dict(row),**updates);return True
        except OSError as exc:
            print('speech_archive_deferred',type(exc).__name__,flush=True);return False

    def remove_audio(self,row):
        if not any(Path(name).exists() for name in (row['audio'],str(Path(row['audio']).with_suffix('.wav')),row['metadata_path'])):return
        if self.archive:self.archive.save(dict(row))
        for name in [row['audio'],str(Path(row['audio']).with_suffix('.wav')),str(Path(row['audio']).with_suffix('.stream')),row['metadata_path']]:
            file=Path(name).resolve()
            if self.root in file.parents:
                try:file.unlink()
                except FileNotFoundError:pass

    def cleanup(self):
        with self.lock,self.db:
            rows=self.db.execute("SELECT * FROM incoming WHERE status='submitted' OR created<?",(time.time()-86400,)).fetchall()
            for row in rows:
                try:self.remove_audio(row)
                except OSError:
                    print('speech_archive_cleanup_deferred',flush=True);continue
                if row['created']<time.time()-86400:self.db.execute('DELETE FROM incoming WHERE id=?',(row['id'],))


def review_records(database):
    file=Path(database)
    if not file.exists():return []
    with sqlite3.connect('file:'+str(file)+'?mode=ro',uri=True,timeout=5) as db:
        db.row_factory=sqlite3.Row
        return [dict(r) for r in db.execute("SELECT id,status,text,error,created FROM incoming WHERE status IN ('needs_review','failed') ORDER BY created DESC LIMIT 30")]
