"""A new isolated broker + worker processes, using a synthetic Codex executable."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request,urlopen

REPO=Path(__file__).resolve().parents[1]

class FreshProcessesTest(unittest.TestCase):
 def test_new_broker_and_worker_complete_a_synthetic_question(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);config=root/'config';data=root/'data';config.mkdir();data.mkdir()
   with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
   broker=dict(host='127.0.0.1',port=port,database=str(data/'state.sqlite3'),client_token='synthetic-client',worker_token='synthetic-worker',memory_authority='mac',hourly_enabled=False,singapore_weather_enabled=False)
   (config/'server-config.json').write_text(json.dumps(broker))
   fake=root/'synthetic-codex'
   fake.write_text('#!'+sys.executable+'\nimport json,sys\nfrom pathlib import Path\nprompt=sys.stdin.read()\nassert "synthetic question" in prompt\nPath(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(json.dumps(dict(reply="synthetic answer",spoken_text="synthetic answer",should_notify=True,sources=[],memory_updates=[],valid_until="")))\n')
   fake.chmod(0o700)
   worker=dict(base_url=f'http://127.0.0.1:{port}',worker_token='synthetic-worker',state_dir=str(data),codex=str(fake),codex_home=str(root/'codex-home'),memory_authority='mac',interactive_fast=False)
   (config/'worker-config.json').write_text(json.dumps(worker))
   env=dict(os.environ,PERSONAL_AGENT_CONFIG=str(config/'server-config.json'),PERSONAL_AGENT_CONFIG_DIR=str(config),PERSONAL_AGENT_DATA_DIR=str(data),PERSONAL_AGENT_TIMEZONE='UTC')
   children=[]
   def call(path,body=None):
    request=Request(f'http://127.0.0.1:{port}'+path,headers={'Authorization':'Bearer synthetic-client','Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
    with urlopen(request,timeout=2) as r:return json.load(r)
   try:
    with (root/'broker.log').open('w') as log:
     children.append(subprocess.Popen([sys.executable,str(REPO/'src/server.py')],env=env,stdout=log,stderr=log))
    for _ in range(80):
     try:call('/health');break
     except OSError:time.sleep(.05)
    else:self.fail('Fresh broker did not start')
    job=call('/ask',{'text':'synthetic question'})
    with (root/'worker.log').open('w') as log:
     children.append(subprocess.Popen([sys.executable,str(REPO/'src/worker.py')],env=env,stdout=log,stderr=log))
    for _ in range(100):
     result=call('/jobs/'+job['id'])
     if result['done']:break
     time.sleep(.05)
    self.assertTrue(result['done'],(root/'worker.log').read_text())
    self.assertEqual(result['reply'],'synthetic answer')
    self.assertTrue((data/'workspace/memory/memory.sqlite3').is_file())
   finally:
    for child in reversed(children):child.terminate()
    for child in reversed(children):
     try:child.wait(timeout=1)
     except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':unittest.main()
