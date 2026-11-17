"""Self-hosted Bark registration bridge and durable outbound notification sender."""
import hmac
import json
import re
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class BarkDelivery:
    def __init__(self, store, config):
        self.store=store
        self.config=config
        self.prefix='/bark/'+config['registration_secret']
        self.upstream='http://127.0.0.1:'+str(config.get('port',18865))

    def proxy(self, handler, path, query):
        """Bark phone registration uses a private URL instead of broker bearer auth."""
        prefix=path[:len(self.prefix)]
        if not hmac.compare_digest(prefix,self.prefix) or not path.startswith(self.prefix+'/'):
            return False
        route=path[len(self.prefix):]
        # No public relay, arbitrary upstream URLs, or unauthenticated DB info.
        if route not in ('/ping','/healthz','/register'):
            handler.respond(404,{'code':404,'message':'Unknown route'})
            return True
        length=int(handler.headers.get('Content-Length',0))
        if length<0 or length>4096:
            handler.respond(413,{'code':413,'message':'Invalid body size'})
            return True
        body=handler.rfile.read(length) if handler.command=='POST' else None
        request=Request(self.upstream+route+('?' + query if query else ''),data=body,
            headers={'Content-Type':handler.headers.get('Content-Type','application/json')},method=handler.command)
        try:
            with urlopen(request,timeout=12) as response:
                result=json.load(response)
            if route=='/register' and result.get('code')==200:
                key=result.get('data',{}).get('device_key') or result.get('data',{}).get('key')
                self.bind(key)
            handler.respond(200,result)
        except HTTPError as exc:
            handler.respond(exc.code,{'code':exc.code,'message':'Bark request rejected'})
        except Exception as exc:
            print('bark_registration_error',type(exc).__name__,flush=True)
            handler.respond(502,{'code':502,'message':'Push service unavailable'})
        return True

    def bind(self, key):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}',key):
            raise ValueError('Invalid Bark registration')
        store=self.store
        with store.lock,store.db:
            if store.get_meta('bark_device_key')==key: return
            # Registering a different device must not replay old private messages.
            store.db.execute("UPDATE messages SET wake_status='skipped' WHERE wake_status='pending'")
            store.set_meta('bark_device_key',key)
            store.set_meta('delivery_transport','bark_test')
            store.set_meta('push_enabled',False)
            job={'id':uuid.uuid4().hex}
            text='助手推送已经连接。这是一条语音播报测试，请戴好耳机并锁屏，检查能否自动听到这句话。'
            store.add_message(job,'test',text,text,time.time()+1800)

    def enable(self):
        store=self.store
        if not store.get_meta('bark_device_key'): raise ValueError('Register Bark first')
        with store.lock,store.db:
            # Only future results should notify when switching from the old frontend.
            store.db.execute("UPDATE messages SET wake_status='skipped' WHERE wake_status='pending'")
            store.set_meta('push_enabled',True)
            store.set_meta('delivery_transport','bark')

    def test(self):
        if not self.store.get_meta('bark_device_key'): raise ValueError('Register Bark first')
        text='这是一条助手通知测试。如果你不用操作手机就听到了正文，自动语音通道就测试成功了。'
        with self.store.lock,self.store.db:
            self.store.add_message({'id':uuid.uuid4().hex},'test',text,text,time.time()+600)

    def tick(self):
        store=self.store
        key=store.get_meta('bark_device_key')
        if not key: return
        with store.lock,store.db:
            store.db.execute("UPDATE messages SET wake_status='expired' WHERE wake_status='pending' AND (expires<? OR superseded=1)",(time.time(),))
            row=store.db.execute('''SELECT * FROM messages WHERE wake_status='pending'
                AND wake_after<=? AND (kind='test' OR ?=1) ORDER BY created,id LIMIT 1''',
                (time.time(),int(store.get_meta('push_enabled',False)))).fetchone()
            if not row: return
            message=dict(row)
        # APNs has a 4 KiB payload limit. Keep a readable speech summary and
        # retain the full response separately in the authenticated message API.
        text=message['spoken_text']
        if len(text.encode('utf-8'))>2400:
            text=text.encode('utf-8')[:2300].decode('utf-8','ignore')+'。完整回答可以运行听助手回复。'
        payload={'device_key':key,'title':{'progress':'助手进展','test':'助手连接测试'}.get(message['kind'],'生活助手'),
            'body':text,'group':'personal-agent','isArchive':'1','ttl':86400,
            'level':'active','sound':'minuet','id':message['id']}
        error=None
        try:
            request=Request(self.upstream+'/push',data=json.dumps(payload,ensure_ascii=False).encode(),
                headers={'Content-Type':'application/json'},method='POST')
            with urlopen(request,timeout=15) as response: result=json.load(response)
            if result.get('code')!=200: raise RuntimeError('Push rejected')
        except Exception as exc:
            error=type(exc).__name__
        with store.lock,store.db:
            attempts=message['wake_attempts']+1
            store.db.execute('''UPDATE messages SET wake_status=?,wake_attempts=?,wake_after=?,wake_error=?
                WHERE id=? AND wake_status='pending' ''',
                ('sent' if error is None else 'failed' if attempts>=8 else 'pending',attempts,
                 time.time()+min(2**attempts,300),error,message['id']))
        print('push_delivery','accepted' if error is None else error,flush=True)

    def run(self):
        while True:
            try: self.tick()
            except Exception as exc: print('push_loop_error',type(exc).__name__,flush=True)
            time.sleep(2)
