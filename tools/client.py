"""Private broker client; never prints credentials. Configuration stays on disk."""
import argparse,json
from pathlib import Path
from urllib.request import Request,urlopen
p=argparse.ArgumentParser();p.add_argument('--setup',required=True,type=Path)
g=p.add_mutually_exclusive_group();g.add_argument('--text');g.add_argument('--enable-audio',action='store_true');args=p.parse_args()
s=json.loads(args.setup.expanduser().read_text());base=s['url'].rsplit('/',1)[0]
path='/health';data=None
if args.text:path='/ask';data={'text':args.text,'conversation':'phone-main'}
if args.enable_audio:path='/delivery/zello/enable';data={}
headers=dict(s['headers']);headers['Content-Type']='application/json'
with urlopen(Request(base+path,headers=headers,data=json.dumps(data).encode() if data is not None else None),timeout=20) as r:print(json.dumps(json.load(r),ensure_ascii=False,indent=2))
