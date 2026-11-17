"""Portable state snapshots; credentials and recordings are deliberately separate."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import zipfile

FORMAT=1

def digest(p):
    value=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()

def backup(home,output,include_health=False):
    if output.exists():raise ValueError('Backup output already exists')
    if output==home or home in output.parents:raise ValueError('Store backups outside the deployment')
    meta=json.loads((home/'deployment.json').read_text());data=home/'data'
    with tempfile.TemporaryDirectory() as temporary:
        stage=Path(temporary);selected=[]
        broker=data/'state.sqlite3';memory=data/'workspace/memory/memory.sqlite3'
        for p in [broker,memory]:
            if not p.exists():continue
            if p.is_symlink():raise ValueError('Database symlinks are not portable')
            dest=stage/p.relative_to(data);dest.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(dest)) as target:
                source.backup(target)
                if target.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Invalid database')
            selected.append(dest)
        if include_health and (data/'wellness').exists():
            for p in (data/'wellness').rglob('*'):
                if p.is_symlink():raise ValueError('Health symlinks are not portable')
                if p.is_file() and p.suffix=='.json':
                    dest=stage/p.relative_to(data);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest);selected.append(dest)
        manifest=dict(format=FORMAT,role=meta['role'],timezone=meta['timezone'],files={str(p.relative_to(stage)):digest(p) for p in selected},excluded=['credentials','codex-home','recordings','transient audio','pending IPC','logs'])
        with output.open('xb') as file:
            os.chmod(output,0o600)
            with zipfile.ZipFile(file,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('manifest.json',json.dumps(manifest,indent=2))
                for p in selected:archive.write(p,str(p.relative_to(stage)))
    print('Private unencrypted backup created:',output)

def restore(home,source):
    data=home/'data';meta=json.loads((home/'deployment.json').read_text())
    if data.is_symlink() or any(data.iterdir()):raise ValueError('Restore requires an empty data directory in a fresh deployment')
    with zipfile.ZipFile(source) as archive,tempfile.TemporaryDirectory(dir=home) as temporary:
        infos=archive.infolist();names=[i.filename for i in infos]
        if len(names)!=len(set(names)) or len(names)>1000:raise ValueError('Duplicate or excessive backup entries')
        for i in infos:
            name=PurePosixPath(i.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in i.filename or (i.external_attr>>16)&0o170000==0o120000:raise ValueError('Unsafe backup path')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('format')!=FORMAT or manifest.get('role')!=meta['role']:raise ValueError('Incompatible backup format or role')
        expected=manifest['files']
        if set(names)!=set(expected)|{'manifest.json'}:raise ValueError('Backup manifest mismatch')
        stage=Path(temporary)
        for name,checksum in expected.items():
            if name not in ('state.sqlite3','workspace/memory/memory.sqlite3') and not (name.startswith('wellness/') and name.endswith('.json')):raise ValueError('Unsupported state file')
            dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with archive.open(name) as inp,dest.open('xb') as out:shutil.copyfileobj(inp,out)
            dest.chmod(0o600)
            if digest(dest)!=checksum:raise ValueError('Backup checksum mismatch')
            if dest.suffix=='.sqlite3':
                with closing(sqlite3.connect(dest)) as db, db:
                    if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Invalid restored database')
                    # Do not replay old requests, sleep reminders or audio after moving.
                    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'jobs' in tables:db.execute("UPDATE jobs SET status='expired' WHERE status IN ('queued','running')")
                    if 'routine_events' in tables:db.execute("UPDATE routine_events SET status='expired' WHERE status IN ('pending','queued')")
                    if 'messages' in tables:db.execute('UPDATE messages SET superseded=1')
                    if 'wellness_pending' in tables:db.execute('DELETE FROM wellness_pending')
        # Publish only after every entry has been validated.
        for p in stage.iterdir():shutil.move(str(p),str(data/p.name))
        memory=data/'workspace/memory/memory.sqlite3'
        if memory.exists():
            import sys
            sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
            from local_memory import LocalMemory
            m=LocalMemory(memory.parent)
            try:m.export()
            finally:m.close()
    print('Restored state. Configure new credentials/paths and reauthorize macOS before starting.')
