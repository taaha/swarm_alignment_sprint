import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from swarm.checkpoint import NAME, canonical, decode, election_state, load_source, save_checkpoint
from swarm.cli import main
from swarm.controller import Controller
from swarm.election_audit import audit_election
from swarm.state import State
from test_controller import Journal, Workspace
from test_election import ElectionScript


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = State(self.root/'runs'/'work-source')
        self.config = dict(workers=5, rounds=3, election=False, election_seed=42,
            private_messages=True, public_messages=False, manager_model='fake', worker_model='fake',
            manager_thinking='low', worker_thinking='low', manager_call_limit=10, worker_call_limit=10,
            max_tokens=12000, temperature=0.7, run_id='work-source', image_id='frozen-image')
        self.parent.data.update(config=self.config, container_id='parent-container', mlflow_run_id='parent-mlflow')
        self.script = ElectionScript()
        issues = [dict(id=f'I{i}', priority='high', observed='Symptom', expected='Expected') for i in range(100)]
        with patch('swarm.controller.mlflow.log_metrics'), contextlib.redirect_stdout(io.StringIO()):
            Controller(self.parent,self.config,Workspace(),self.script,Journal(self.parent),issues).run()
        self.parent.data['private'].append(dict(sender='worker_1',recipient='worker_2',text='Work-only private memory',round=3))
        self.parent.save()
        self.path, self.digest = save_checkpoint(self.parent)

    def invoke(self, *args, extra_env=None):
        script = ElectionScript()
        script.check = lambda *args: None
        captured=[]
        def telemetry(state, config):
            journal=Journal(state)
            journal.run_id='child-mlflow'
            state.data['mlflow_run_id']='child-mlflow'
            journal.finish=lambda *args,**kwargs: None
            captured.append(journal)
            return journal
        with patch.dict('os.environ',dict(SWARM_DATA=str(self.root),OLLAMA_API_KEY='fake-key',
                SWARM_WORKER_THINKING='high',SWARM_PRIVATE_MESSAGES='false',SWARM_WORKERS='30',**(extra_env or {})),clear=True), \
             patch('sys.argv',['swarm',*args]), patch('swarm.cli.Ollama',return_value=script), \
             patch('swarm.cli.Telemetry',side_effect=telemetry), \
             patch('swarm.cli.Workspace',side_effect=AssertionError('Must not create workspace')), \
             patch('swarm.cli.docker.from_env',side_effect=AssertionError('Must not access Docker')), \
             patch('swarm.election.mlflow.log_metrics'), contextlib.redirect_stdout(io.StringIO()):
            main()
        return captured, script

    def test_two_elections_have_same_start_and_independent_records(self):
        original=self.path.read_bytes()
        snapshot=self.parent.db.execute('SELECT body FROM snapshot').fetchone()[0]
        events=self.parent.db.execute('SELECT count(*) FROM events').fetchone()[0]
        starts=[]
        for name in ['election-a','election-b']:
            captured,script=self.invoke('resume-election','--from-run','work-source','--run-id',name)
            child=captured[0].state
            self.assertEqual(child.data['status'],'completed')
            self.assertEqual(len(child.data['turns']),45)
            self.assertIsNone(child.data['container_id'])
            self.assertEqual(child.data['lineage']['checkpoint_sha256'],self.digest)
            self.assertEqual(child.data['lineage']['source_mlflow_run_id'],'parent-mlflow')
            self.assertEqual(child.data['config']['worker_thinking'],'low')
            self.assertTrue(child.data['config']['private_messages'])
            self.assertEqual(child.data['config']['workers'],5)
            self.assertEqual(child.data['config']['concurrency'],5)
            child_events=[dict(kind=r['kind'],body=json.loads(r['body'])) for r in child.db.execute('SELECT * FROM events ORDER BY id')]
            self.assertFalse(any(e['kind']=='patch_application_start' for e in child_events))
            self.assertEqual(len([e for e in child_events if e['kind']=='turn_start']),26)
            self.assertTrue(audit_election(child.data,child_events,initial_private=self.parent.data['private'])['future_plan_isolation_verified'])
            starts.append([c for c in script.contexts if c['phase']=='discussion_1'])
            # This only cleans up the child, never the parent's container.
            self.invoke('cleanup','--run-id',name)
        self.assertEqual(starts[0],starts[1])
        for ctx in starts[0]:
            self.assertEqual(ctx['scratchpad'],self.parent.data['scratchpads'][ctx['agent']])
            self.assertEqual(ctx['public_channel'],self.parent.data['public'])
            self.assertEqual(ctx['election_board'],[])
        self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(self.parent.db.execute('SELECT body FROM snapshot').fetchone()[0],snapshot)
        self.assertEqual(self.parent.db.execute('SELECT count(*) FROM events').fetchone()[0],events)

    def test_explicit_overrides_and_false_thinking_inheritance(self):
        captured,_=self.invoke('resume-election','--from-run','work-source','--run-id','overrides',
            '--private-messages=false','--worker-thinking','medium','--manager-thinking','off',
            '--worker-model','other-model','--election-seed','7','--concurrency','2')
        cfg=captured[0].state.data['config']
        self.assertEqual(cfg['concurrency'],2)
        self.assertEqual(cfg['worker_model'],'other-model')
        self.assertEqual(cfg['worker_thinking'],'medium')
        self.assertIs(cfg['manager_thinking'],False)
        self.assertFalse(cfg['private_messages'])
        self.assertEqual(cfg['election_seed'],7)
        self.assertFalse(any(m['phase'].startswith('discussion') for m in captured[0].state.data['private'] if 'phase' in m))

    def test_rejects_overrides_invalid_paths_and_duplicate_destinations(self):
        for arg in ['--workers','--rounds','--public-messages','--election']:
            with self.assertRaises(ValueError):
                self.invoke('resume-election','--from-run','work-source','--run-id','bad',arg,'false' if arg in ['--public-messages','--election'] else '5')
        for source in ['../work-source','missing','']:
            with self.assertRaises(ValueError): load_source(self.root,source)
        self.invoke('resume-election','--from-run','work-source','--run-id','existing')
        before=(self.root/'runs/existing/state.sqlite').read_bytes()
        with self.assertRaises(FileExistsError):
            self.invoke('resume-election','--from-run','work-source','--run-id','existing')
        self.assertEqual((self.root/'runs/existing/state.sqlite').read_bytes(),before)

    def test_legacy_work_source_is_loaded_without_writing_checkpoint(self):
        self.path.unlink()
        source,raw,digest=load_source(self.root,'work-source')
        self.assertEqual(source,self.parent.data)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),digest)
        self.assertFalse(self.path.exists())
        self.assertEqual(election_state(source,'work-source',digest)['status'],'election_pending')

    def test_rejects_tampering_and_overwrite(self):
        raw=self.path.read_bytes()
        changed=json.loads(raw);changed['state']['scratchpads']['worker_1']='tampered'
        self.path.write_bytes(canonical(changed))
        with self.assertRaises(ValueError): load_source(self.root,'work-source')
        self.path.write_bytes(raw)
        self.parent.data['scratchpads']['worker_1']='new notes'
        with self.assertRaises(ValueError): save_checkpoint(self.parent)
        self.assertEqual(self.path.read_bytes(),raw)
        changed=json.loads(raw);changed['version']=900
        with self.assertRaises(ValueError): decode(canonical(changed))

    def test_rejects_incomplete_and_election_contaminated_legacy_sources(self):
        self.path.unlink()
        original=copy.deepcopy(self.parent.data)
        for changes in [dict(status='running'),dict(election={}),dict(assignments={'worker_1':'I1'}),dict(completed_rounds=2)]:
            self.parent.data={**copy.deepcopy(original),**changes};self.parent.save()
            with self.assertRaises(ValueError): load_source(self.root,'work-source')
