"""Agent-facing local memory tool; uses no server, tokens, or external network."""
import argparse,json,os,sys
from pathlib import Path
from local_memory import LocalMemory


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['list','read','search','apply']);parser.add_argument('values',nargs='*');args=parser.parse_args()
    root=Path.cwd();memory=LocalMemory(root/'memory',readonly=args.action!='apply')
    try:
        if args.action=='list':value=memory.index()
        elif args.action=='read':value=memory.read(args.values)
        elif args.action=='search':value=memory.search(' '.join(args.values))
        else:
            context=json.loads((root/'memory-task.json').read_text())
            if time_expired(context):raise ValueError('Memory task context expired')
            changes=json.load(sys.stdin)
            if not isinstance(changes,list):raise ValueError('Changes must be a JSON array')
            value=memory.apply(changes,context)
        print(json.dumps(value,ensure_ascii=False))
    finally:memory.close()


def time_expired(context):
    import time
    return time.time()-context['created']>900


if __name__=='__main__':main()
