"""Candidate authenticated conversation history and unified voice ingress."""
import json
import re


def valid_conversation(value):
    return isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',value)


def dispatch(handler,store,path,query,payload):
    method=handler.command
    if method=='GET' and path=='/conversations':
        with store.lock:
            current=store.get_meta('phone_conversation','phone-main')
            rows=store.db.execute('''SELECT conversation,count(*) AS turns,max(created) AS updated
                FROM jobs WHERE kind='ask' AND (conversation='phone-main' OR conversation LIKE 'phone-%')
                GROUP BY conversation ORDER BY updated DESC LIMIT 100''').fetchall()
            values=[dict(row) for row in rows]
            if not any(row['conversation']==current for row in values):
                values.insert(0,dict(conversation=current,turns=0,updated=None))
        handler.respond(200,dict(current=current,conversations=values));return True
    if method=='GET' and re.fullmatch('/conversations/[a-zA-Z0-9_-]{1,80}',path):
        conversation=path.rsplit('/',1)[1]
        with store.lock:
            if conversation=='current':conversation=store.get_meta('phone_conversation','phone-main')
            limit=max(1,min(100,int(query.get('limit',['50'])[0])))
            before=query.get('before',[''])[0]
            args=[conversation];where="kind='ask' AND conversation=?"
            if before:
                cursor=store.job(before)
                if not cursor or cursor['conversation']!=conversation:raise ValueError('Invalid history cursor')
                where+=' AND (created<? OR (created=? AND id<?))'
                args.extend([cursor['created'],cursor['created'],cursor['id']])
            rows=store.db.execute('SELECT * FROM jobs WHERE '+where+' ORDER BY created DESC,id DESC LIMIT ?',args+[limit+1]).fetchall()
            more=len(rows)>limit;rows=rows[:limit];turns=[]
            for row in reversed(rows):
                item=store.public_job(dict(row))
                progress=store.db.execute("SELECT reply FROM messages WHERE job_id=? AND kind='progress' ORDER BY created DESC LIMIT 1",(row['id'],)).fetchone()
                item.update(question=row['question'],input_source=row['input_source'] or 'text',
                    progress=progress[0] if progress and not item['done'] else '')
                turns.append(item)
            current=store.get_meta('phone_conversation','phone-main')
        handler.respond(200,dict(conversation=conversation,current=current,turns=turns,
            next_before=rows[-1]['id'] if more else None));return True
    if method=='POST' and path=='/conversation/select':
        conversation=payload.get('conversation')
        if not valid_conversation(conversation):raise ValueError('Invalid conversation')
        with store.lock:
            exists=store.db.execute("SELECT 1 FROM jobs WHERE kind='ask' AND conversation=? LIMIT 1",(conversation,)).fetchone()
            if not exists and conversation!=store.get_meta('phone_conversation','phone-main'):
                raise ValueError('Unknown conversation')
            store.set_meta('phone_conversation',conversation)
        handler.respond(200,dict(ok=True,current=conversation));return True
    if method=='POST' and path=='/worker/audio/ask':
        source_id=payload.get('source_id','')
        if not isinstance(source_id,str) or not re.fullmatch(r'[a-f0-9-]{36}-[0-9]+',source_id):
            raise ValueError('Invalid audio message id')
        with store.lock:
            conversation=store.get_meta('phone_conversation','phone-main')
            job=store.enqueue('（语音消息，正在识别）',conversation,dedup='voice:'+source_id,
                input_source='zello_audio',input_metadata={'source_id':source_id,'recognition':'codex_webrtc'})
        handler.respond(202,store.public_job(job));return True
    if method=='POST' and path=='/worker/ask':
        text=payload.get('text','')
        source_id=payload.get('source_id','')
        if not isinstance(text,str) or not 1<=len(text.strip())<=8000:raise ValueError('Invalid transcript')
        if not isinstance(source_id,str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,160}',source_id):
            raise ValueError('Invalid voice message id')
        metadata=payload.get('metadata',{})
        if not isinstance(metadata,dict):raise ValueError('Invalid input metadata')
        metadata={key:metadata[key] for key in ('sender','language','recognition') if key in metadata}
        with store.lock:
            conversation=store.get_meta('phone_conversation','phone-main')
            job=store.enqueue(text.strip(),conversation,dedup='voice:'+source_id,
                input_source='zello',input_metadata=metadata)
        handler.respond(202,store.public_job(job));return True
    return False
