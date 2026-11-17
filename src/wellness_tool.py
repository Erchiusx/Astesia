from runtime_paths import CONFIG_DIR, DATA_DIR
"""Read only; canonical health data lives outside the agent's writable workspace."""
import argparse,json
from pathlib import Path
from wellness_data import WellnessSnapshot
from routine_store import METRICS
p=argparse.ArgumentParser();p.add_argument('action',choices=['status','summary','read']);p.add_argument('metric',nargs='?',choices=METRICS);args=p.parse_args()
v=WellnessSnapshot(DATA_DIR/'wellness').load()
if args.action=='status':v={k:value for k,value in v.items() if k!='metrics'}
elif args.action=='read':
 if not args.metric:p.error('read requires a metric')
 v=dict(status=v['status'],captured_at=v.get('captured_at'),metric=args.metric,data=v['metrics'].get(args.metric,dict(status='no_samples')))
print(json.dumps(v,ensure_ascii=False))
