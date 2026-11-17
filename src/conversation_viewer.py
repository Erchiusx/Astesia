from runtime_paths import CONFIG_DIR, DATA_DIR
"""Candidate private loopback viewer; browser never receives the broker token."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.error import HTTPError
from urllib.parse import urlparse,parse_qs,urlencode
from urllib.request import Request,urlopen
from voice_inbox import review_records

ROOT=Path(__file__).resolve().parent


class Viewer(BaseHTTPRequestHandler):
    def log_message(self,*_):pass

    def reply(self,status,body,mime='application/json'):
        if mime=='application/json':body=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',mime+'; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Content-Length',str(len(body)));self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass

    def handle_request(self):
        port=self.server.server_port
        hosts={'127.0.0.1:'+str(port),'localhost:'+str(port)}
        if self.headers.get('Host','') not in hosts:return self.reply(403,{'error':'Local access only'})
        origin=self.headers.get('Origin')
        if origin and origin not in {'http://'+h for h in hosts}:return self.reply(403,{'error':'Invalid origin'})
        if self.headers.get('Sec-Fetch-Site')=='cross-site':return self.reply(403,{'error':'Invalid origin'})
        parsed=urlparse(self.path)
        static={'/':('viewer.html','text/html'),'/app.js':('viewer.js','text/javascript'),'/style.css':('viewer.css','text/css')}
        if self.command=='GET' and parsed.path in static:
            filename,mime=static[parsed.path];body=(ROOT/'viewer'/filename).read_bytes()
            if filename=='viewer.html':body=body.replace(b'__NONCE__',self.server.nonce.encode())
            return self.reply(200,body,mime)
        if not hmac.compare_digest(self.headers.get('X-Personal-Agent-Nonce',''),self.server.nonce):
            return self.reply(403,{'error':'Reload this local page'})
        if not parsed.path.startswith('/api/'):return self.reply(404,{'error':'Not found'})
        remote=parsed.path[4:]
        if self.command=='GET' and remote=='/memories' and getattr(self.server,'local_memory_path',None):
            with sqlite3.connect('file:'+str(self.server.local_memory_path)+'?mode=ro',uri=True,timeout=5) as db:
                db.row_factory=sqlite3.Row
                import time
                records=[dict(row) for row in db.execute('SELECT key,category,text,version,expires,updated FROM memory_items WHERE deleted=0 AND (expires IS NULL OR expires>?) ORDER BY updated DESC,key',(time.time(),))]
            return self.reply(200,dict(authority='mac',memories=records))
        if self.command=='GET' and remote=='/voice-inbox':
            return self.reply(200,dict(messages=review_records(self.server.inbox_database)))
        match=re.fullmatch('/voice-inbox/([a-f0-9-]{36}-[0-9]+)/(audio|submit)',remote)
        if match:
            return self.inbox_action(match.group(1),match.group(2))
        allowed_get=remote in ('/health','/conversations','/memories') or re.fullmatch('/conversations/[a-zA-Z0-9_-]{1,80}',remote)
        allowed_post=remote in ('/ask','/conversation/new','/conversation/select')
        if not ((self.command=='GET' and allowed_get) or (self.command=='POST' and allowed_post)):
            return self.reply(404,{'error':'Not found'})
        body=None
        if self.command=='POST':
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=65536:return self.reply(413,{'error':'Request too large'})
            value=json.loads(self.rfile.read(size))
            if not isinstance(value,dict):return self.reply(400,{'error':'Invalid request'})
            if remote=='/ask':value['input_source']='web'
            body=json.dumps(value,ensure_ascii=False).encode()
        query=parse_qs(parsed.query)
        query={key:value[0] for key,value in query.items() if key in ('before','limit')}
        url=self.server.base+remote+('?' + urlencode(query) if query else '')
        request=Request(url,data=body,headers={'Authorization':self.server.authorization,'Content-Type':'application/json'},method=self.command)
        try:
            with urlopen(request,timeout=25) as response:
                data=response.read(2*1024*1024)
                if 'application/json' in response.headers.get('Content-Type',''):
                    return self.reply(response.status,json.loads(data))
                return self.reply(response.status,{'message':data.decode()})
        except HTTPError as exc:
            return self.reply(exc.code,{'error':'Broker request rejected','status':exc.code})

    def inbox_action(self,key,action):
        database=self.server.inbox_database
        if not database.exists():return self.reply(404,{'error':'No voice inbox'})
        with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True,timeout=5) as db:
            db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM incoming WHERE id=?',(key,)).fetchone()
        if not row:return self.reply(404,{'error':'Unknown voice message'})
        if action=='audio' and self.command=='GET':
            wav=Path(row['audio']).with_suffix('.wav').resolve()
            if self.server.inbox_root not in wav.parents or not wav.is_file() or wav.stat().st_size>20*1024*1024:
                return self.reply(404,{'error':'Audio is unavailable'})
            return self.reply(200,wav.read_bytes(),'audio/wav')
        if action!='submit' or self.command!='POST':return self.reply(405,{'error':'Invalid method'})
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=65536:return self.reply(413,{'error':'Request too large'})
        value=json.loads(self.rfile.read(size));text=value.get('text','') if isinstance(value,dict) else ''
        if not isinstance(text,str) or not 1<=len(text.strip())<=8000:return self.reply(400,{'error':'Invalid transcript'})
        if row['status'] not in ('needs_review','failed'):return self.reply(409,{'error':'Message is already handled'})
        request=Request(self.server.base+'/ask',data=json.dumps(dict(text=text.strip(),input_source='zello_review'),ensure_ascii=False).encode(),
            headers={'Authorization':self.server.authorization,'Content-Type':'application/json','Idempotency-Key':'voice:'+key})
        with urlopen(request,timeout=25) as response:job=json.load(response)
        with sqlite3.connect(database,timeout=5) as db:
            import time
            db.execute("UPDATE incoming SET status='submitted',text=?,job_id=?,updated=? WHERE id=?",(text.strip(),job['id'],time.time(),key))
        return self.reply(202,job)

    def do_GET(self):
        try:self.handle_request()
        except Exception:return self.reply(502,{'error':'Service unavailable'})

    do_POST=do_GET


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    if not config.get('enable_viewer',False):raise SystemExit('Viewer is not enabled')
    setup=json.loads(Path(config['client_setup_path']).expanduser().read_text())
    base=setup['url'].rsplit('/',1)[0]
    if urlparse(base).scheme!='https':raise SystemExit('HTTPS broker required')
    server=ThreadingHTTPServer(('127.0.0.1',int(config.get('viewer_port',18770))),Viewer)
    server.nonce=secrets.token_urlsafe(32);server.base=base
    server.authorization=setup['headers']['Authorization']
    memory_config=json.loads((CONFIG_DIR/'worker-config.json').read_text()) if (CONFIG_DIR/'worker-config.json').exists() else config
    server.local_memory_path=Path(memory_config['state_dir'])/'workspace/memory/memory.sqlite3' if memory_config.get('memory_authority')=='mac' else None
    server.inbox_database=Path(config.get('voice_inbox_database',DATA_DIR/'voice-inbox.sqlite3')).expanduser().resolve()
    server.inbox_root=Path(config.get('voice_inbox_dir',DATA_DIR/'voice-inbox')).expanduser().resolve()
    print('Local conversation viewer ready on port',server.server_port,flush=True)
    server.serve_forever()


if __name__=='__main__':main()
