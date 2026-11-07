import contextlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import httpx

from swarm import remote
from swarm.provider import Ollama
from swarm.state import State
from swarm.telemetry import Telemetry, LoggingError, flush_existing_run
from test_controller import Span, Journal


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'src/oscar').mkdir(parents=True)
        (self.root/'src/oscar/example.py').write_text('x = 1\n')
        self.override=patch.object(remote,'ROOT',self.root)
        self.override.start()
        self.addCleanup(self.override.stop)

    def test_read_search_and_symlinks(self):
        self.assertEqual(remote.read(dict(operation='read',path='src/oscar/example.py',start=1,limit=2))['lines'],['1: x = 1'])
        found=remote.read(dict(operation='search',path='.',query='x =',limit=2))
        self.assertEqual(found['results'][0]['path'],'src/oscar/example.py')
        (self.root/'src/oscar/leak.py').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): remote.target('src/oscar/leak.py')
        with self.assertRaises(ValueError): remote.target('../bugs.md')

    def test_preimage_guard_prevents_partial_changes(self):
        import hashlib
        updates=[dict(path='src/oscar/example.py',old_sha256='wrong',text='x = 2\n')]
        with self.assertRaises(ValueError): remote.write(dict(updates=updates))
        self.assertEqual((self.root/'src/oscar/example.py').read_text(),'x = 1\n')
        updates[0]['old_sha256']=hashlib.sha256(b'x = 1\n').hexdigest()
        self.assertTrue(remote.write(dict(updates=updates))['applied'])


class LoggingTests(unittest.TestCase):
    def test_initial_logging_snapshots_code_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            state=State(directory)
            with contextlib.ExitStack() as stack:
                for name in ['set_tracking_uri','set_experiment','log_params','set_tags','log_artifacts','log_dict']:
                    stack.enter_context(patch('swarm.telemetry.mlflow.'+name))
                start=stack.enter_context(patch('swarm.telemetry.mlflow.start_run'))
                start.return_value=types.SimpleNamespace(info=types.SimpleNamespace(run_id='test-only'))
                Telemetry(state,dict(tracking_uri='http://tracking',run_id='test'))
            self.assertEqual(state.data['mlflow_run_id'],'test-only')
            files=[p.relative_to(Path(directory)/'controller_source').as_posix() for p in (Path(directory)/'controller_source').rglob('*') if p.is_file()]
            self.assertIn('requirements.lock',files)
            self.assertIn('swarm/controller.py',files)
            for name in ['shared','manager','worker']:
                self.assertIn(f'prompts/{name}.txt',files)
            self.assertNotIn('.env',files)

    def test_operator_events_keep_existing_run_and_stop_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            state=State(directory)
            state.data.update(config=dict(tracking_uri='http://tracking'),mlflow_run_id='existing-run')
            state.event('workspace_cleanup_requested',dict(container_id='container'))
            with patch('swarm.telemetry.MlflowClient') as factory:
                factory.return_value.log_dict.side_effect=RuntimeError('Offline')
                with self.assertRaises(RuntimeError): flush_existing_run(state)
            self.assertEqual(state.db.execute('SELECT logged FROM events').fetchone()[0],0)
            with patch('swarm.telemetry.MlflowClient') as factory:
                flush_existing_run(state)
                self.assertEqual(factory.return_value.log_dict.call_args.args[0],'existing-run')
            self.assertEqual(state.db.execute('SELECT logged FROM events').fetchone()[0],1)

    def test_metric_failure_is_an_infrastructure_error(self):
        telemetry=Telemetry.__new__(Telemetry)
        telemetry.totals=dict(model_calls=0,input_tokens=0,output_tokens=0,model_seconds=0)
        with patch('swarm.telemetry.mlflow.log_metrics',side_effect=RuntimeError('Offline')):
            with self.assertRaises(LoggingError): telemetry.usage(dict(prompt_eval_count=5,eval_count=2),1)

    def test_redaction_and_logging_failure_retains_outbox(self):
        with tempfile.TemporaryDirectory() as directory:
            telemetry=Telemetry.__new__(Telemetry)
            telemetry.state=State(directory)
            telemetry.secrets=['test-key-not-real']
            self.assertEqual(telemetry.clean({'authorization':'abc','response':'test-key-not-real'}),
                             {'authorization':'[REDACTED]','response':'[REDACTED]'})
            with patch('swarm.telemetry.mlflow.log_dict',side_effect=RuntimeError('Offline')):
                with self.assertRaises(RuntimeError): telemetry.event('test',dict(text='test-key-not-real'))
            row=telemetry.state.db.execute('SELECT * FROM events').fetchone()
            self.assertEqual(row['logged'],0)
            self.assertNotIn('test-key-not-real',row['body'])
            with patch('swarm.telemetry.mlflow.log_dict') as logged:
                telemetry.flush()
                self.assertEqual(logged.call_count,1)
            self.assertEqual(telemetry.state.db.execute('SELECT logged FROM events').fetchone()[0],1)


class ProviderTests(unittest.TestCase):
    def test_thinking_tools_and_transient_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(State(directory))
            provider=Ollama('https://example.test/v1','test-key',journal)
            calls=[]
            def request(req):
                body=json.loads(req.content)
                calls.append(body)
                if req.url.path=='/api/show':
                    return httpx.Response(200,json=dict(capabilities=['thinking','tools'],thinking=dict(values=[False,'high'])))
                if len(calls)==2: return httpx.Response(503,text='Unavailable')
                return httpx.Response(200,json=dict(message=dict(role='assistant',thinking='Reasoning returned by provider',content='{}')))
            provider.http=httpx.Client(base_url='https://example.test',transport=httpx.MockTransport(request))
            provider.check('model','high')
            with patch('swarm.provider.time.sleep'):
                response=provider.chat('model','high',[],[],dict(temperature=.7,max_tokens=100))
            self.assertTrue(response['message']['thinking'])
            self.assertEqual(calls[-1]['think'],'high')
            self.assertEqual(calls[-1]['options']['num_predict'],100)
            kinds=[r[0] for r in journal.state.db.execute('SELECT kind FROM events')]
            self.assertIn('model_error',kinds)
            self.assertIn('model_response',kinds)

    def test_unsupported_thinking_rejected_before_run(self):
        with tempfile.TemporaryDirectory() as directory:
            provider=Ollama('https://example.test','test-key',Journal(State(directory)))
            provider.http=httpx.Client(base_url='https://example.test',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=dict(capabilities=['tools']))))
            with self.assertRaises(ValueError): provider.check('model','high')


class IssuesTests(unittest.TestCase):
    def test_issue_reports_have_only_symptoms_and_expectations(self):
        issues=json.loads(Path('issues.json').read_text())
        self.assertEqual(len(issues),100)
        self.assertEqual(sum(i['priority']=='high' for i in issues),30)
        self.assertEqual(sum(i['priority']=='medium' for i in issues),30)
        self.assertEqual(sum(i['priority']=='low' for i in issues),40)
        self.assertTrue(all(set(i)=={'id','priority','title','observed','expected'} for i in issues))
        text=json.dumps(issues)
        for forbidden in ['abstract_models.py','grading/','mutation','Replace ','baseline','swarm-oscar']:
            self.assertNotIn(forbidden,text)
