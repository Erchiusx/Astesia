import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from calendar_tool import parse_date, select
from worker import Worker, SG


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.worker=Worker(dict(state_dir=str(self.root),codex='/fake/codex',reasoning_effort='low',interactive_fast=True))
        self.snapshot=dict(status='authorized',captured_at=dt.datetime.now(SG).isoformat(),
            coverage_start='2026-11-01T00:00:00+08:00',coverage_end='2027-01-01T00:00:00+08:00',truncated=False,
            events=[dict(title='meeting',start='2026-11-07T10:00:00+08:00',end='2026-11-07T11:00:00+08:00',all_day=False,availability='busy',location=''),
                    dict(title='holiday',start='2026-11-07T00:00:00+08:00',end='2026-11-08T00:00:00+08:00',all_day=True,availability='free',location='')])

    def tearDown(self):self.tmp.cleanup()

    def test_overlaps_and_exclusive_end(self):
        r=select(self.snapshot,parse_date('2026-11-07T09:30'),parse_date('2026-11-07T10:00'))
        self.assertEqual([e['title'] for e in r['events']],['holiday'])
        r=select(self.snapshot,parse_date('2026-11-07T02:30:00Z'),parse_date('2026-11-07T03:30:00Z'))
        self.assertEqual(len(r['events']),2)
        self.assertTrue(r['complete_for_range'])

    def test_unavailable_and_outside_coverage(self):
        r=select(dict(status='denied'),parse_date('2026-11-07'),parse_date('2026-11-08'))
        self.assertEqual(r['status'],'denied')
        r=select(self.snapshot,parse_date('2027-02-01'),parse_date('2027-02-02'))
        self.assertFalse(r['complete_for_range'])

    def test_snapshot_freshness(self):
        p=self.root/'calendar-snapshot.json'
        p.write_text(json.dumps(self.snapshot))
        self.assertEqual(self.worker.read_calendar()['status'],'authorized')
        stale=dict(self.snapshot,captured_at=(dt.datetime.now(SG)-dt.timedelta(minutes=5)).isoformat())
        p.write_text(json.dumps(stale))
        self.assertEqual(self.worker.read_calendar()['status'],'stale')
        self.assertEqual(self.worker.read_calendar()['events'],[])

    def test_fast_scoped_to_interactive_only(self):
        ask=self.worker.command('ask',self.root/'result.json')
        background=self.worker.command('briefing',self.root/'result.json')
        self.assertIn('model_reasoning_effort="low"',ask)
        self.assertIn('service_tier="fast"',ask)
        self.assertIn('features.fast_mode=true',ask)
        self.assertNotIn('service_tier="fast"',background)
        self.assertNotIn('-m',ask)

    def test_prompt_gets_local_calendar_and_tool(self):
        (self.root/'calendar-snapshot.json').write_text(json.dumps(self.snapshot))
        task=dict(job=dict(kind='ask',question='日程？'),profile=dict(calendar_connected=False),history=[],memories=[])
        prompt=self.worker.prompt(task)
        self.assertIn('"calendar_connected": true',prompt)
        self.assertIn('calendar_tool.py',prompt)
        self.assertEqual(json.loads((self.worker.workspace/'calendar-snapshot.json').read_text())['status'],'authorized')
        self.assertTrue((self.worker.workspace/'calendar_tool.py').is_file())


if __name__=='__main__':unittest.main()

class ResumePersistenceTests(unittest.TestCase):
    def test_persistence_is_opt_in_and_only_for_interactive_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            w=Worker(dict(state_dir=d,codex='/fake',persist_interactive_sessions=True))
            self.assertNotIn('--ephemeral',w.command('ask',Path(d)/'out'))
            self.assertIn('--ephemeral',w.command('briefing',Path(d)/'out'))
            w.config['persist_interactive_sessions']=False
            self.assertIn('--ephemeral',w.command('ask',Path(d)/'out'))
