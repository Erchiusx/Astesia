"""Mac-authoritative versioned memory, selective reads and local recovery copies."""
import hashlib,json,os,re,sqlite3,time
from pathlib import Path
from memory_store import MemoryStore


class LocalMemory:
    def __init__(self,root,readonly=False):
        self.root=Path(root)
        if not readonly:self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'memory.sqlite3'
        if readonly:
            self.db=sqlite3.connect('file:'+str(self.path)+'?mode=ro',uri=True,timeout=15);self.db.row_factory=sqlite3.Row
            self.store=MemoryStore.__new__(MemoryStore);self.store.db=self.db;self.store.active=self.active
            return
        self.db=sqlite3.connect(self.path,timeout=15);self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE IF NOT EXISTS memories(text TEXT,evidence TEXT,created REAL);
          CREATE TABLE IF NOT EXISTS local_receipts(id TEXT PRIMARY KEY,report TEXT);
        ''')
        self.store=MemoryStore(self.db);self.store.active=self.active;self.path.chmod(0o600)

    def close(self):self.db.close()

    def active(self):
        return [dict(row) for row in self.db.execute('SELECT key,category,text,version,expires,updated FROM memory_items WHERE deleted=0 AND (expires IS NULL OR expires>?) ORDER BY updated DESC,key',(time.time(),))]

    def index(self):
        return [dict(key=r['key'],category=r['category'],version=r['version'],expires=r['expires'],file='memory/records/'+r['key']+'.json') for r in self.active()]

    def read(self,keys):
        if not keys or len(keys)>20:raise ValueError('Read 1 to 20 keys')
        return [dict(r) for r in self.db.execute('SELECT * FROM memory_items WHERE key IN ('+','.join('?' for _ in keys)+')',keys)]

    def search(self,query):
        return [dict(r) for r in self.db.execute('SELECT key,category,text,version FROM memory_items WHERE deleted=0 AND (expires IS NULL OR expires>?) AND (text LIKE ? OR key LIKE ?) LIMIT 20',(time.time(),'%'+query+'%','%'+query+'%'))]

    def checkpoint(self):
        folder=self.root/'backups';folder.mkdir(mode=0o700,exist_ok=True)
        name=folder/(str(time.time_ns())+'.sqlite3')
        with sqlite3.connect(name) as target:self.db.backup(target)
        name.chmod(0o600)
        for old in sorted(folder.glob('*.sqlite3'),reverse=True)[30:]:old.unlink()

    def apply(self,updates,context,receipt=None):
        if not updates:return dict(accepted=[],rejected=[])
        receipt=receipt or context['job_id']+':'+hashlib.sha256(json.dumps(updates,sort_keys=True).encode()).hexdigest()
        old=self.db.execute('SELECT report FROM local_receipts WHERE id=?',(receipt,)).fetchone()
        if old:return json.loads(old[0])
        self.checkpoint()
        with self.db:
            report=self.store.apply(updates,context['question'],context['job_id'],context['kind'])
            self.db.execute('INSERT INTO local_receipts VALUES (?,?)',(receipt,json.dumps(report)))
        self.export();return report

    def seed(self,data):
        if self.db.execute('SELECT count(*) FROM memory_items').fetchone()[0]:raise ValueError('Local memory is not empty')
        with self.db:
            for record in data['items']:
                self.db.execute('INSERT INTO memory_items VALUES (?,?,?,?,?,?,?,?,?,?)',tuple(record[k] for k in ['key','category','text','evidence','source_job','created','updated','expires','version','deleted']))
            for a in data['audit']:
                self.db.execute('INSERT INTO memory_audit(id,key,operation,before_json,after_json,job_id,reason,created) VALUES (?,?,?,?,?,?,?,?)',tuple(a[k] for k in ['id','key','operation','before_json','after_json','job_id','reason','created']))
        self.export();self.checkpoint()

    def export(self):
        folder=self.root/'records';folder.mkdir(mode=0o700,exist_ok=True)
        for record in self.db.execute('SELECT * FROM memory_items'):
            key=record['key']
            if not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}',key):raise ValueError('Invalid stored key')
            self.write(folder/(key+'.json'),dict(record))
        self.write(self.root/'INDEX.json',self.index())

    @staticmethod
    def write(path,value):
        temporary=path.with_name(path.name+'.tmp')
        with temporary.open('w') as out:json.dump(value,out,ensure_ascii=False,indent=2);out.flush();os.fsync(out.fileno())
        temporary.chmod(0o600);temporary.replace(path)
