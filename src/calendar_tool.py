"""Read the task-frozen calendar snapshot. Never calls a calendar write API."""
import argparse
import datetime as dt
import json
from pathlib import Path

from runtime_paths import TIMEZONE as SG


def parse_date(value):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    return result.replace(tzinfo=SG) if result.tzinfo is None else result


def select(snapshot, start, end, query=''):
    if start >= end:
        raise ValueError('End must be later than start')
    if snapshot.get('status') != 'authorized':
        return dict(status=snapshot.get('status', 'unavailable'), events=[])
    events = [event for event in snapshot.get('events', [])
              if parse_date(event['start']) < end and parse_date(event['end']) > start
              and (not query or query.casefold() in (event.get('title','')+' '+event.get('location','')).casefold())]
    return dict(status='authorized', captured_at=snapshot.get('captured_at'),
                coverage_start=snapshot.get('coverage_start'), coverage_end=snapshot.get('coverage_end'),
                complete_for_range=(not snapshot.get('truncated',False)
                    and parse_date(snapshot['coverage_start']) <= start
                    and parse_date(snapshot['coverage_end']) >= end),
                events=events)


def main():
    parser=argparse.ArgumentParser(description='Query the read-only local calendar snapshot')
    parser.add_argument('--start', default=dt.datetime.now(SG).date().isoformat())
    parser.add_argument('--end')
    parser.add_argument('--query', default='')
    args=parser.parse_args()
    start=parse_date(args.start)
    end=parse_date(args.end) if args.end else start+dt.timedelta(days=7)
    snapshot=json.loads((Path(__file__).parent/'calendar-snapshot.json').read_text())
    print(json.dumps(select(snapshot,start,end,args.query),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
