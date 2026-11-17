import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
import manage
from tools.migration import backup,restore
from server import Store
from local_memory import LocalMemory
from test_memory import change

class DeploymentTests(unittest.TestCase):
 def test_init_install_and_private_state_backup_restore(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);home=root/'first';other=root/'second'
   manage.init(home,'broker','UTC');manage.init(other,'broker','UTC')
   cfg=json.loads((home/'config/server-config.json').read_text())
   self.assertNotEqual(cfg['client_token'],cfg['worker_token']);self.assertTrue(manage.doctor(home))
   manage.install(home);manage.install(home)
   self.assertTrue((home/'current/server.py').is_file())
   s=Store(home/'data/state.sqlite3','client','worker');s.enqueue('synthetic request');s.routine.signal({'event':'wake'})
   m=LocalMemory(home/'data/workspace/memory');q='I like quiet routes.';m.apply([change('Quiet routes',q)],dict(job_id='test',kind='ask',question=q))
   (home/'data/private-recording.wav').write_bytes(b'not for export')
   target=root/'backup.zip';backup(home,target)
   with zipfile.ZipFile(target) as archive:
    self.assertNotIn('private-recording.wav',archive.namelist());self.assertFalse(any('config' in p for p in archive.namelist()))
   restore(other,target)
   self.assertNotEqual(json.loads((other/'config/server-config.json').read_text())['client_token'],cfg['client_token'])
   with sqlite3.connect(other/'data/state.sqlite3') as db:
    self.assertEqual(db.execute('select status from jobs').fetchone()[0],'expired')
    self.assertEqual(db.execute('select status from routine_events').fetchone()[0],'expired')
   restored=LocalMemory(other/'data/workspace/memory');self.assertEqual(restored.active()[0]['text'],'Quiet routes');restored.close()
   self.assertTrue((other/'data/workspace/memory/INDEX.json').exists());s.db.close();m.close()
 def test_restore_rejects_traversal_and_leaves_data_empty(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);home=root/'target';manage.init(home,'broker','UTC')
   archive=root/'bad.zip'
   with zipfile.ZipFile(archive,'w') as z:
    z.writestr('../escape','bad');z.writestr('manifest.json','{}')
   with self.assertRaises(ValueError):restore(home,archive)
   self.assertEqual(list((home/'data').iterdir()),[])
 def test_restore_rejects_bad_checksum_without_partial_publish(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);home=root/'target';manage.init(home,'broker','UTC');archive=root/'bad.zip'
   with zipfile.ZipFile(archive,'w') as z:
    z.writestr('wellness/latest.json','{}');z.writestr('manifest.json',json.dumps(dict(format=1,role='broker',files={'wellness/latest.json':'wrong'})))
   with self.assertRaises(ValueError):restore(home,archive)
   self.assertEqual(list((home/'data').iterdir()),[])
 def test_runtime_paths_and_timezone_are_deployment_specific(self):
  with tempfile.TemporaryDirectory() as temporary:
   env=dict(os.environ,PYTHONPATH=str(REPO/'src'),PERSONAL_AGENT_CONFIG_DIR=temporary+'/config',PERSONAL_AGENT_DATA_DIR=temporary+'/data',PERSONAL_AGENT_TIMEZONE='America/New_York')
   code="from runtime_paths import *; print(CONFIG_DIR);print(DATA_DIR);print(TIMEZONE)"
   r=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,check=True)
   self.assertIn('America/New_York',r.stdout);self.assertIn(temporary+'/data',r.stdout)

if __name__=='__main__':unittest.main()

class ShortcutTests(unittest.TestCase):
 def test_generation_needs_no_installed_workflow_and_upload_follows_review(self):
  import plistlib
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);setup=root/'setup.json';setup.write_text(json.dumps(dict(url='https://agent.example.com/personal-agent/briefing',headers={'Authorization':'Bearer synthetic-test'})))
   subprocess.run([sys.executable,str(REPO/'tools/build_shortcuts.py'),'--setup',str(setup),'--output',str(root/'shortcuts'),'--unsigned'],check=True,capture_output=True)
   paths=list((root/'shortcuts/unsigned').glob('*.shortcut'));self.assertEqual(len(paths),3)
   manual=plistlib.loads((root/'shortcuts/unsigned/问生活助手.unsigned.shortcut').read_bytes())['WFWorkflowActions']
   names=[a['WFWorkflowActionIdentifier'] for a in manual]
   self.assertLess(names.index('is.workflow.actions.ask'),names.index('is.workflow.actions.downloadurl'))
   self.assertEqual(manual[0]['WFWorkflowActionParameters']['WFDictateTextStopListening'],'On Tap')
   self.assertEqual(names.count('is.workflow.actions.downloadurl'),1)

class ReleaseTests(unittest.TestCase):
 def test_reinstall_preserves_previous_release_and_rollback_selects_it(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary);home=root/'deployment';repo=root/'source';(repo/'src').mkdir(parents=True)
   (repo/'VERSION').write_text('test');(repo/'src/server.py').write_text('# first release\n')
   manage.init(home,'broker','UTC')
   with patch.object(manage,'REPO',repo):
    manage.install(home);first=(home/'current').resolve()
    (repo/'src/server.py').write_text('# second release\n');manage.install(home)
    self.assertNotEqual((home/'current').resolve(),first)
    manage.install(home);self.assertEqual((home/'previous').resolve(),first)
    manage.rollback(home);self.assertEqual((home/'current').resolve(),first)
