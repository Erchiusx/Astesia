import datetime as dt
from pathlib import Path
import tempfile
import time
import unittest

from server import Store


def change(text,evidence,**kwargs):
    value=dict(operation='upsert',key='preference.walking_routes',category='preference',
        text=text,evidence=evidence,evidence_source='user',evidence_key='',version=0,
        expires_at='',confidence=1,reason='user preference')
    value.update(kwargs);return value


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'state.sqlite3'
        self.store=Store(self.path,'client','worker')

    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()

    def apply(self,items,question,kind='ask'):
        with self.store.db:return self.store.memory.apply(items,question,'test-job',kind)

    def test_autonomous_preference_correction_delete_and_reintroduction(self):
        q='I like quiet walking routes.'
        self.assertEqual(self.apply([change('Quiet routes',q)],q)['accepted'],['preference.walking_routes'])
        q='Actually I prefer lively routes now.'
        self.apply([change('Lively routes',q,version=1)],q)
        self.assertEqual(len(self.store.memory.active()),1)
        self.assertEqual(self.store.memory.active()[0]['text'],'Lively routes')
        q='Forget my walking preference.'
        self.apply([change('',q,operation='delete',version=2)],q)
        self.assertEqual(self.store.memory.active(),[])
        self.assertEqual(self.store.memory.retired_versions()[0]['version'],3)
        self.assertTrue(self.apply([change('Quiet routes',q,version=1)],q)['rejected'])
        q='I like quiet routes again.'
        self.apply([change('Quiet routes',q,version=3)],q)
        self.assertEqual(self.store.memory.active()[0]['version'],4)
        self.assertEqual(self.store.db.execute('select count(*) from memory_audit').fetchone()[0],4)

    def test_sources_uncertainty_duplicates_and_background_are_checked(self):
        q='What is the weather?'
        self.assertTrue(self.apply([change('Invented fact','I like quiet routes')],q)['rejected'])
        q='I like quiet routes.'
        self.assertTrue(self.apply([change('Quiet routes',q,confidence=.6)],q)['rejected'])
        self.assertTrue(self.apply([change('Quiet routes',q)],q,'briefing')['rejected'])
        self.apply([change('Quiet routes',q)],q)
        self.assertTrue(self.apply([change('Quiet routes',q,key='duplicate.fact')],q)['rejected'])
        self.assertTrue(self.apply([change('Invented fact','not in memory',key='another.fact',
            evidence_source='memory',evidence_key='preference.walking_routes')],q)['rejected'])
        report=self.apply([change('', 'Quiet routes',operation='delete',version=1,
            evidence_source='memory',evidence_key='preference.walking_routes')],'Organize existing memory')
        self.assertTrue(report['accepted'])

    def test_ongoing_expiry_and_malformed_changes_do_not_destroy_valid_memory(self):
        q='I am preparing an exam this week.'
        item=change('Exam this week',q,key='ongoing.exam',category='ongoing')
        self.assertTrue(self.apply([item],q)['rejected'])
        until=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=7)).isoformat()
        item['expires_at']=until;self.assertTrue(self.apply([item],q)['accepted'])
        bad=change('Bad date',q,key='bad.date',expires_at=123)
        self.assertTrue(self.apply([bad,None],q)['rejected'])
        self.assertEqual(len(self.store.memory.active()),1)
        with self.store.db:self.store.db.execute('update memory_items set expires=?',(time.time()-1,))
        self.assertEqual(self.store.memory.active(),[])
        self.assertEqual(self.store.memory.retired_versions()[0]['key'],'ongoing.exam')

    def test_legacy_migration_is_idempotent_and_never_resurrects_deleted_memory(self):
        with self.store.db:
            self.store.db.execute("delete from meta where key='memory_v2_migrated'")
            self.store.db.execute('insert into memories values (?,?,?)',('Old fact','Explicit evidence',time.time()))
        self.store.db.close();self.store=Store(self.path,'client','worker')
        old=self.store.memory.active()[0]
        self.assertEqual(old['text'],'Old fact')
        self.apply([change('', 'Old fact',key=old['key'],operation='delete',version=1,
            evidence_source='memory',evidence_key=old['key'])],'Clean up')
        self.store.db.close();self.store=Store(self.path,'client','worker')
        self.assertEqual(self.store.memory.active(),[])
        self.assertEqual(self.store.db.execute('select count(*) from memories').fetchone()[0],1)

    def test_reset_conversation_keeps_memory_and_new_task_receives_versions(self):
        q='I like quiet routes.';self.apply([change('Quiet routes',q)],q)
        self.store.set_meta('phone_conversation','fresh-conversation')
        self.store.enqueue('Hello','fresh-conversation');task=self.store.claim()
        self.assertEqual(task['memories'],['Quiet routes'])
        self.assertEqual(task['memory_records'][0]['version'],1)


if __name__=='__main__':unittest.main()
