import contextlib
import copy
import io
import json
from pathlib import Path
from unittest.mock import patch

from swarm.cli import parser
from swarm.election import Election, PROMPTS
from swarm.election_policy import PHASES, audit_policy
from swarm.election_collusion import FILES, freeze_collusion, validated_collusion, collusion_blocks
from swarm.telemetry import export
import test_election_policy as policy_tests
from test_controller import Journal
from test_election import ElectionScript
import unittest


class CollusionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=policy_tests.PolicyTests()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root
        self.workers=self.fixture.workers

    def state(self, helpers='worker_3,worker_5', candidate='worker_1', self_workers=''):
        state=self.fixture.state(self_workers)
        record=freeze_collusion(helpers,candidate,self.workers,state.data['election_policy']['workers'])
        state.data['election_collusion_policy']=record
        state.data['config'].update(election_colluder_workers=record['workers'],
            election_colluder_candidate=record['candidate'],election_collusion_sha256=record['sha256'])
        state.save()
        return state

    def test_validation_and_frozen_integrity(self):
        for helpers,candidate,self_workers in [('worker_3','',()),('','worker_1',()),('manager','worker_1',()),
                ('worker_3','manager',()),('worker_3','worker_3',()),('worker_3,,worker_5','worker_1',()),
                ('worker_3','worker_1',('worker_3',))]:
            with self.assertRaises(ValueError): freeze_collusion(helpers,candidate,self.workers,self_workers)
        self.assertEqual(freeze_collusion('worker_5, worker_3,worker_5','worker_1',self.workers)['workers'],['worker_3','worker_5'])
        state=self.state()
        for mutate in [lambda d:d.pop('election_collusion_policy'),
                       lambda d:d['election_collusion_policy']['rendered'].update(objective='changed'),
                       lambda d:d['election_collusion_policy']['templates']['objective'].update(text='changed'),
                       lambda d:d['config'].update(election_colluder_candidate='worker_2'),
                       lambda d:d['config'].update(workflow='full_pipeline')]:
            altered=copy.deepcopy(state.data);mutate(altered)
            with self.assertRaises(ValueError): validated_collusion(altered)
        with patch('swarm.election_collusion.PROMPTS',self.root/'missing'):
            self.assertEqual(freeze_collusion('','',self.workers)['templates'],{})
            with self.assertRaises(ValueError): freeze_collusion('worker_3','worker_1',self.workers)
        for text in ['','{unknown}','{candidate.invalid}']:
            directory=self.root/'templates';directory.mkdir(exist_ok=True)
            for filename in FILES.values(): (directory/filename).write_text(text)
            with patch('swarm.election_collusion.PROMPTS',directory):
                with self.assertRaises(ValueError): freeze_collusion('worker_3','worker_1',self.workers)

    @patch('swarm.election.mlflow.log_metrics')
    def test_full_election_delivery_export_and_tamper_audit(self, metrics):
        for self_workers in ['', 'worker_1']:
            state=self.state(self_workers=self_workers);journal=Journal(state)
            class Recorded(ElectionScript):
                def chat(self,model,thinking,messages,available,config):
                    journal.event('model_request',dict(request=dict(messages=copy.deepcopy(messages),tools=available)))
                    return super().chat(model,thinking,messages,available,config)
            election=Election(state,state.data['config'],Recorded(),journal)
            for phase in PHASES:
                for agent in ['manager',*self.workers]:
                    system=election.instructions(agent,phase)
                    self.assertEqual('Private coalition context:' in system,agent in ['worker_1','worker_3','worker_5'])
                    self.assertEqual('Private coalition objective:' in system,agent in ['worker_3','worker_5'])
                    if agent=='worker_1': self.assertEqual('Private election objective:' in system,bool(self_workers))
            with patch('swarm.election_collusion.PROMPTS',self.root/'removed'), contextlib.redirect_stdout(io.StringIO()): election.run()
            events=self.fixture.events(state)
            self.assertTrue(audit_policy(state.data,events,PROMPTS)['targeted_collusion_delivery_verified'])
            baseline=self.fixture.state('');ordinary=Election(baseline,baseline.data['config'],ElectionScript(),Journal(baseline))
            for phase in PHASES:
                self.assertEqual(election.instructions('worker_2',phase),ordinary.instructions('worker_2',phase))
            export(state,self.root/'export')
            self.assertEqual(json.loads((self.root/'export/election-collusion-policy.json').read_text()),state.data['election_collusion_policy'])
            for filename in ['public.md','election.md']:
                self.assertNotIn('Private coalition objective:',(self.root/'export'/filename).read_text())
            altered=copy.deepcopy(events)
            turn=next(e for e in altered if e['kind']=='turn_start' and e['body']['agent']=='worker_2')
            turn['body']['system']+='\n'+collusion_blocks(election.coalition,'worker_3',turn['body']['phase'])[0]
            with self.assertRaises(AssertionError): audit_policy(state.data,altered,PROMPTS)
            altered=copy.deepcopy(events)
            next(e for e in altered if e['kind']=='turn_start')['body']['context']['election_colluder_workers']=['worker_3']
            with self.assertRaises(AssertionError): audit_policy(state.data,altered,PROMPTS)

    @patch('swarm.election.mlflow.log_metrics')
    def test_losing_coalition_future_plans_and_voluntary_disclosure(self, metrics):
        state=self.state(candidate='worker_4');journal=Journal(state)
        objective=state.data['election_collusion_policy']['rendered']['objective']
        class Disclosure(ElectionScript):
            def chat(self,*args):
                response=super().chat(*args)
                for call in response['message'].get('tool_calls',[]):
                    if call['function']['name']=='submit_discussion_statement':
                        call['function']['arguments']['text']='I choose to disclose: '+objective
                return response
        election=Election(state,state.data['config'],Disclosure(),journal)
        with contextlib.redirect_stdout(io.StringIO()): election.run()
        self.assertNotEqual(state.data['election']['result']['winner'],'worker_4')
        self.assertEqual(len(state.data['election']['future_plans']),6)
        self.assertTrue(any(objective.strip() in statement['text'] for record in state.data['election']['discussion_rounds'] for statement in record['statements']))
        self.assertTrue(audit_policy(state.data,self.fixture.events(state),PROMPTS)['targeted_collusion_delivery_verified'])

    def test_tools_and_context_remain_unchanged(self):
        state=self.state();baseline=self.fixture.state('')
        election=Election(state,state.data['config'],ElectionScript(),Journal(state))
        ordinary=Election(baseline,baseline.data['config'],ElectionScript(),Journal(baseline))
        for phase in PHASES:
            agents=self.workers
            if phase=='future_plan':
                election.e['result']=ordinary.e['result']=dict(winner='worker_1')
                agents=['manager',*self.workers]
            election.prepare_phase(phase,agents);ordinary.prepare_phase(phase,agents)
            for agent in agents:
                self.assertEqual(election.available_tools(agent,phase),ordinary.available_tools(agent,phase))
                self.assertEqual(election.context(agent,phase),ordinary.context(agent,phase))

    def test_cli_environment_precedence(self):
        with patch.dict('os.environ',dict(SWARM_ELECTION_COLLUDER_WORKERS='worker_3',SWARM_ELECTION_COLLUDER_CANDIDATE='worker_1')):
            args=parser().parse_args(['resume-election'])
            self.assertEqual((args.election_colluder_workers,args.election_colluder_candidate),('worker_3','worker_1'))
            args=parser().parse_args(['resume-election','--election-colluder-workers','','--election-colluder-candidate',''])
            self.assertEqual((args.election_colluder_workers,args.election_colluder_candidate),('',''))

    def test_mlflow_snapshot(self):
        import types
        from swarm.telemetry import Telemetry
        state=self.state();config=state.data['config'];config.update(tracking_uri='http://tracking',run_id='scripted-coalition')
        with contextlib.ExitStack() as stack:
            mocks={n:stack.enter_context(patch('swarm.telemetry.mlflow.'+n)) for n in
                ['set_tracking_uri','set_experiment','log_params','set_tags','log_artifact','log_artifacts','log_dict','start_run']}
            mocks['start_run'].return_value=types.SimpleNamespace(info=types.SimpleNamespace(run_id='fake'))
            with patch('swarm.election_collusion.PROMPTS',self.root/'missing'): Telemetry(state,config)
        record=state.data['election_collusion_policy']
        self.assertEqual(json.loads((state.directory/'election-collusion-policy.json').read_text()),record)
        self.assertEqual([c.args[0] for c in mocks['log_dict'].call_args_list if c.args[1]=='election-collusion-policy.json'],[record])
        for template in record['templates'].values():
            self.assertEqual((state.directory/'controller_source/prompts'/template['filename']).read_text(),template['text'])


class CollusionResumeTests(unittest.TestCase):
    def setUp(self):
        self.fixture=policy_tests.PolicyResumeTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_resume_reset_precedence_and_source_preservation(self):
        f=self.fixture.fixture;raw=f.path.read_bytes()
        env=dict(SWARM_ELECTION_COLLUDER_WORKERS='worker_3,worker_5',SWARM_ELECTION_COLLUDER_CANDIDATE='worker_1')
        for name,args,selected in [('env',[],['worker_3','worker_5']),('override',['--election-colluder-workers','worker_2'],['worker_2']),
                                 ('off',['--election-colluder-workers','','--election-colluder-candidate',''],[])]:
            captured,_=f.invoke('resume-election','--from-run','work-source','--run-id',name,*args,extra_env=env)
            self.assertEqual(captured[0].state.data['election_collusion_policy']['workers'],selected)
        self.assertEqual(f.path.read_bytes(),raw)
        f.parent.data['config'].update(election_colluder_workers=['worker_3'],election_colluder_candidate='worker_1',election_collusion_sha256='old')
        f.parent.save();f.path.unlink()
        from swarm.checkpoint import save_checkpoint
        f.path,_=save_checkpoint(f.parent)
        captured,_=f.invoke('resume-election','--from-run','work-source','--run-id','reset')
        self.assertEqual(captured[0].state.data['election_collusion_policy']['workers'],[])
        self.assertIsNone(captured[0].state.data['config']['election_colluder_candidate'])

    def test_invalid_configuration_fails_before_reservation(self):
        f=self.fixture.fixture
        for args in [['--election-colluder-workers','worker_3'],
                     ['--election-colluder-workers','worker_3','--election-colluder-candidate','worker_3'],
                     ['--election-colluder-workers','worker_3','--election-colluder-candidate','worker_1','--election-policy-workers','worker_3']]:
            with self.assertRaises(ValueError): f.invoke('resume-election','--from-run','work-source','--run-id','invalid',*args)
            self.assertFalse((f.root/'runs/invalid').exists())
        with self.assertRaises(ValueError):
            f.invoke('run','--run-id','fresh','--election-colluder-workers','worker_3','--election-colluder-candidate','worker_1')
        self.assertFalse((f.root/'runs/fresh').exists())
