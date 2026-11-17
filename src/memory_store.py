"""Versioned personal memory with source checks, expiry and an audit trail."""
import datetime as dt
import hashlib
import json
import re
import time


class MemoryStore:
    def __init__(self,db):
        self.db=db
        db.executescript('''
            CREATE TABLE IF NOT EXISTS memory_items (
              key TEXT PRIMARY KEY, category TEXT, text TEXT, evidence TEXT,
              source_job TEXT, created REAL, updated REAL, expires REAL,
              version INTEGER, deleted INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS memory_audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, operation TEXT,
              before_json TEXT, after_json TEXT, job_id TEXT, reason TEXT, created REAL);
        ''')
        # Migrate once, preserving the original table for rollback.
        if not db.execute("SELECT 1 FROM meta WHERE key='memory_v2_migrated'").fetchone():
            for row in db.execute('SELECT text,evidence,created FROM memories').fetchall():
                key='legacy.'+hashlib.sha256(row['text'].encode()).hexdigest()[:16]
                db.execute('INSERT OR IGNORE INTO memory_items VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (key,'profile',row['text'],row['evidence'],'legacy',row['created'],row['created'],None,1,0))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('memory_v2_migrated','true')")
        db.commit()

    def active(self):
        return [dict(row) for row in self.db.execute('''SELECT key,category,text,version,expires,updated
            FROM memory_items WHERE deleted=0 AND (expires IS NULL OR expires>?)
            ORDER BY updated DESC,key LIMIT 120''',(time.time(),))]

    def retired_versions(self):
        return [dict(row) for row in self.db.execute('''SELECT key,version FROM memory_items
            WHERE deleted=1 OR expires<=? ORDER BY updated DESC LIMIT 120''',(time.time(),))]

    def apply(self,updates,question,job_id,kind):
        report={'accepted':[],'rejected':[]}
        if not isinstance(updates,list):return report
        sources={row['key']:row for row in self.active()}
        for change in updates[:8]:
            key=change.get('key','') if isinstance(change,dict) else ''
            try:
                if kind!='ask':raise ValueError('interactive_only')
                if not isinstance(change,dict):raise ValueError('invalid_change')
                evidence=change.get('evidence','')
                # Old workers remain supported during rollout, with the old gate.
                if 'operation' not in change:
                    if not evidence or evidence not in question or not re.search(r'记住|以后|偏好|remember|prefer',evidence,re.I):
                        raise ValueError('missing_user_source')
                    text=change.get('text','')
                    key='legacy.'+hashlib.sha256(str(text).encode()).hexdigest()[:16]
                    old=self.db.execute('SELECT * FROM memory_items WHERE key=?',(key,)).fetchone()
                    change=dict(operation='upsert',key=key,category='profile',text=text,evidence=evidence,
                        evidence_source='user',evidence_key='',version=old['version'] if old else 0,
                        expires_at='',confidence=1,reason='explicit user memory')
                if not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}',key):raise ValueError('invalid_key')
                if not isinstance(evidence,str) or not evidence.strip():raise ValueError('missing_source')
                source=change.get('evidence_source')
                if source=='user':
                    if evidence not in question:raise ValueError('missing_user_source')
                elif source=='memory':
                    record=sources.get(change.get('evidence_key',''))
                    if not record or evidence not in record['text']:raise ValueError('missing_memory_source')
                else:raise ValueError('invalid_source')
                operation=change.get('operation')
                if operation not in ('upsert','delete'):raise ValueError('invalid_operation')
                old=self.db.execute('SELECT * FROM memory_items WHERE key=?',(key,)).fetchone()
                expected=change.get('version')
                if type(expected)!=int or expected!=(old['version'] if old else 0):raise ValueError('version_conflict')
                confidence=change.get('confidence',0)
                if type(confidence) not in (int,float) or not .85<=confidence<=1:raise ValueError('uncertain_fact')
                now=time.time();before=dict(old) if old else None
                if operation=='delete':
                    if not old or old['deleted']:raise ValueError('not_active')
                    self.db.execute('UPDATE memory_items SET deleted=1,version=version+1,updated=?,source_job=? WHERE key=?',(now,job_id,key))
                else:
                    text=change.get('text');category=change.get('category')
                    if not isinstance(text,str) or not 1<=len(text.strip())<=500:raise ValueError('invalid_text')
                    if category not in ('profile','preference','interaction','ongoing'):raise ValueError('invalid_category')
                    expires=None;expiry=change.get('expires_at','')
                    if not isinstance(expiry,str):raise ValueError('invalid_expiry')
                    if expiry:
                        until=dt.datetime.fromisoformat(expiry.replace('Z','+00:00'))
                        if until.tzinfo is None:raise ValueError('expiry_needs_timezone')
                        expires=until.timestamp()
                        if not now<expires<=now+366*86400:raise ValueError('invalid_expiry')
                    if category=='ongoing' and expires is None:raise ValueError('ongoing_needs_expiry')
                    duplicate=self.db.execute('SELECT key FROM memory_items WHERE text=? AND deleted=0 AND key!=? AND (expires IS NULL OR expires>?)',(text.strip(),key,now)).fetchone()
                    if duplicate:raise ValueError('duplicate_fact')
                    self.db.execute('''INSERT INTO memory_items VALUES (?,?,?,?,?,?,?,?,?,0)
                        ON CONFLICT(key) DO UPDATE SET category=excluded.category,text=excluded.text,
                        evidence=excluded.evidence,source_job=excluded.source_job,updated=excluded.updated,
                        expires=excluded.expires,version=excluded.version,deleted=0''',
                        (key,category,text.strip(),evidence,job_id,old['created'] if old else now,now,expires,(old['version']+1) if old else 1))
                after=dict(self.db.execute('SELECT * FROM memory_items WHERE key=?',(key,)).fetchone())
                self.db.execute('INSERT INTO memory_audit(key,operation,before_json,after_json,job_id,reason,created) VALUES (?,?,?,?,?,?,?)',
                    (key,operation,json.dumps(before,ensure_ascii=False),json.dumps(after,ensure_ascii=False),job_id,str(change.get('reason',''))[:300],now))
                report['accepted'].append(key)
            except (ValueError,TypeError,KeyError,OverflowError) as exc:
                report['rejected'].append({'key':str(key)[:80],'error':str(exc)[:80]})
        return report
