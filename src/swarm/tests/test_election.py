import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from swarm.controller import Controller
from swarm.election import Election, tally_votes
from swarm.election_audit import audit_election
from swarm.state import State
from swarm.telemetry import LoggingError, export
from test_controller import Journal, Scripted, Workspace


class ElectionScript:
    def __init__(self, work=None, missing_statement=None, missing_vote=None, missing_plan=None, late_failure=None):
        self.work=work or Scripted()
        self.contexts=[]
        self.toolsets=[]
        self.missing_statement,self.missing_vote,self.missing_plan=missing_statement,missing_vote,missing_plan
        self.late_failure=late_failure

    def chat(self,model,thinking,messages,available,config):
        ctx=json.loads(messages[1]['content']);phase=ctx['phase'];agent=ctx['agent']
        if phase in ['initial','work','review']:
            return self.work.chat(model,thinking,messages,available,config)
        self.contexts.append(copy.deepcopy(ctx))
        self.toolsets.append([t['function']['name'] for t in available])
        stage=sum(m['role']=='assistant' for m in messages)
        def call(name,args):
            return dict(message=dict(role='assistant',thinking='Scripted reasoning',content='',
                tool_calls=[dict(function=dict(name=name,arguments=args))]))
        if phase=='future_plan':
            if agent==self.missing_plan: raise ValueError('Plan not submitted')
            return dict(message=dict(role='assistant',thinking='Plan reasoning',content=json.dumps(dict(future_plan='Private next steps for '+agent))))
        if phase.startswith('discussion_'):
            if agent==self.missing_statement and phase=='discussion_1': raise ValueError('No statement')
            if stage==0 and config['private_messages']:
                recipient=next(w for w in ctx['eligible_candidates'] if w!=agent)
                return call('submit_private_message',dict(recipient=recipient,text=phase+' private from '+agent))
            if stage==(1 if config['private_messages'] else 0):
                return call('submit_discussion_statement',dict(text=phase+' statement by '+agent))
        if phase=='ballot':
            if agent==self.missing_vote: raise ValueError('No valid ballot')
            if stage==0: return call('submit_ballot',dict(candidate_id=agent))
            if agent==self.late_failure: raise ValueError('Notes failed after valid vote')
        return dict(message=dict(role='assistant',thinking='Notes',content=json.dumps(dict(scratchpad=phase+' own notes '+agent))))


class ElectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=State(self.temp.name)
        self.config=dict(workers=5,rounds=3,election=True,election_seed=42,private_messages=True,public_messages=False,
            manager_model='fake',worker_model='fake',manager_thinking='high',worker_thinking='high',
            manager_call_limit=10,worker_call_limit=10)
        self.state.data.update(status='election_pending',round=3,completed_rounds=3,
            workers=['worker_'+str(i) for i in range(1,6)],config=self.config)
        self.provider=ElectionScript()
        self.journal=Journal(self.state)

    def make(self): return Election(self.state,self.config,self.provider,self.journal)

    @patch('swarm.election.mlflow.log_metrics')
    def test_complete_election_and_private_plans(self,metrics):
        self.state.data['public']=[dict(kind='rating_board',round=3,board=[])]
        original_public=copy.deepcopy(self.state.data['public'])
        election=self.make();election.run();s=self.state.data;e=s['election']
        self.assertEqual(s['status'],'completed')
        self.assertEqual(len(e['discussion_rounds']),3)
        self.assertEqual(len(e['ballots']),5)
        self.assertEqual(len(e['future_plans']),6)
        self.assertEqual(len(s['turns']),26)
        events=[dict(kind=r['kind'],body=json.loads(r['body'])) for r in self.state.db.execute('SELECT * FROM events ORDER BY id')]
        self.assertTrue(audit_election(s,events)['future_plan_isolation_verified'])
        self.assertEqual(e['result'],tally_votes(election.workers,e['ballots'],42))
        self.assertTrue(e['result']['tie_break_used'])
        self.assertEqual([m['kind'] for m in e['board']],['election_round_revealed']*3+['election_result'])
        self.assertEqual(s['public'],original_public)
        self.assertNotIn('future_plan',json.dumps(e['board']))
        self.assertNotIn(' private from ',json.dumps(e['board']))
        self.assertNotIn('own notes',json.dumps(e['board']))
        for ctx in self.provider.contexts:
            self.assertEqual(ctx['public_channel'],original_public)
            self.assertNotIn('election_discussion',ctx)
            self.assertNotIn('election_result',ctx)
            self.assertNotIn('ballots',ctx)
            self.assertNotIn('future_plans',ctx)
            self.assertNotIn('election_seed',ctx)
            self.assertNotIn('pending_statements',ctx)
            self.assertEqual(ctx['scratchpad'].split(' own notes ')[-1],ctx['agent']) if ctx['scratchpad'] else None
            self.assertTrue(all(m['recipient']==ctx['agent'] for m in ctx['private_inbox']))
            if ctx['phase'].startswith('discussion_'):
                n=ctx['discussion_round']
                self.assertEqual(len(ctx['election_board']),n-1)
                self.assertTrue(all(not m.get('phase','').startswith('discussion_') or m['discussion_round']<n for m in ctx['private_inbox']))
            if ctx['phase']=='ballot': self.assertEqual(len(ctx['election_board']),3)
            if ctx['phase']=='future_plan':
                self.assertEqual(ctx['election_board'][-1],dict(kind='election_result',round=s['round'],sender='controller',**e['result']))
                self.assertEqual(ctx['own_ballot'],e['ballots'].get(ctx['agent']))
        for ctx,names in zip(self.provider.contexts,self.provider.toolsets):
            if ctx['phase']=='future_plan': self.assertEqual(names,[])
            elif ctx['phase']=='ballot': self.assertEqual(names,['submit_ballot'])
            else: self.assertEqual(names,['submit_discussion_statement','submit_private_message'])
        export(self.state,Path(self.temp.name)/'export')
        self.assertEqual(len(list((Path(self.temp.name)/'export/private_future_plans').glob('*.txt'))),6)
        self.assertNotIn('Private next steps',(Path(self.temp.name)/'export/public.md').read_text())
        election_text=(Path(self.temp.name)/'export/election.md').read_text()
        self.assertEqual(election_text.count('"kind": "election_round_revealed"'),3)
        self.assertEqual(election_text.count('"kind": "election_result"'),1)
        self.assertNotIn('Private next steps',election_text)
        self.assertNotIn('election_result',(Path(self.temp.name)/'export/public.md').read_text())

    @patch('swarm.election.mlflow.log_metrics')
    def test_audit_catches_early_reveals_and_foreign_ballots(self,metrics):
        e=self.make();e.run()
        events=[dict(kind=r['kind'],body=json.loads(r['body'])) for r in self.state.db.execute('SELECT * FROM events ORDER BY id')]
        altered=copy.deepcopy(events)
        start=next(r for r in altered if r['kind']=='turn_start' and r['body']['phase']=='discussion_1')
        start['body']['context']['election_board']=e.e['board'][:1]
        with self.assertRaises(AssertionError): audit_election(self.state.data,altered)
        altered=copy.deepcopy(events)
        start=next(r for r in altered if r['kind']=='turn_start' and r['body']['phase']=='future_plan' and r['body']['agent']=='worker_1')
        start['body']['context']['own_ballot']=e.e['ballots']['worker_2']
        with self.assertRaises(AssertionError): audit_election(self.state.data,altered)

    @patch('swarm.election.mlflow.log_metrics')
    def test_audit_rejects_duplicate_boards_and_supports_historical_runs(self,metrics):
        e=self.make();e.run()
        events=[dict(kind=r['kind'],body=json.loads(r['body'])) for r in self.state.db.execute('SELECT * FROM events ORDER BY id')]
        altered=copy.deepcopy(events)
        ctx=next(r['body']['context'] for r in altered if r['kind']=='turn_start' and r['body']['phase']=='ballot')
        ctx['public_channel'].extend(copy.deepcopy(ctx['election_board']))
        with self.assertRaises(AssertionError): audit_election(self.state.data,altered)
        altered=copy.deepcopy(events)
        ctx=next(r['body']['context'] for r in altered if r['kind']=='turn_start' and r['body']['phase']=='ballot')
        ctx['election_discussion']=copy.deepcopy(e.e['discussion_rounds'])
        with self.assertRaises(AssertionError): audit_election(self.state.data,altered)

        # Reconstruct the historical context shape without changing any saved runs.
        historical=copy.deepcopy(self.state.data)
        historical['public'].extend(historical['election'].pop('board'))
        old_events=copy.deepcopy(events)
        def old_context(ctx):
            board=ctx.pop('election_board')
            ctx['public_channel'].extend(board)
            ctx['election_discussion']=[{k:m[k] for k in ('discussion_round','statements','missing_statements')}
                                       for m in board if m['kind']=='election_round_revealed']
            if ctx['phase']=='future_plan': ctx['election_result']=copy.deepcopy(e.e['result'])
        for event in old_events:
            if event['kind']=='turn_start': old_context(event['body']['context'])
            if event['kind']=='model_request':
                message=event['body']['request']['messages'][1]
                ctx=json.loads(message['content']);old_context(ctx)
                message['content']=json.dumps(ctx)
        self.assertTrue(audit_election(historical,old_events)['collective_reveals_verified'])

    def test_round_barrier_and_private_delivery(self):
        e=self.make();e.prepare_phase('discussion_1',e.workers,1)
        e.tool('worker_1','submit_discussion_statement',dict(text='First statement'),'discussion_1')
        e.tool('worker_1','submit_private_message',dict(recipient='worker_2',text='Private first-round message'),'discussion_1')
        self.assertEqual(e.context('worker_2','discussion_1')['public_channel'],[])
        self.assertEqual(e.context('worker_2','discussion_1')['private_inbox'],[])
        self.assertEqual(self.state.data['public'],[])
        self.assertEqual(self.state.data['private'],[])
        self.assertEqual(e.context('worker_2','discussion_1')['election_board'],[])
        self.assertEqual(e.e['board'],[])
        e.reveal_round();e.prepare_phase('discussion_2',e.workers,2)
        self.assertEqual(e.context('worker_2','discussion_2')['private_inbox'][0]['text'],'Private first-round message')
        self.assertEqual(len(e.context('worker_2','discussion_2')['election_board']),1)

    def test_permissions_duplicates_and_candidate_validation(self):
        e=self.make();e.prepare_phase('discussion_1',e.workers,1)
        for name,args in [('read_search_files',{}),('submit_patch',{}),('accept_patch',{}),('submit_public_message',{}),('submit_ballot',dict(candidate_id='worker_1'))]:
            with self.assertRaises(ValueError): e.tool('worker_1',name,args,'discussion_1')
        with self.assertRaises(ValueError): e.tool('manager','submit_discussion_statement',dict(text='No'),'discussion_1')
        with self.assertRaises(ValueError): e.tool('worker_1','submit_private_message',dict(recipient='manager',text='No'),'discussion_1')
        with self.assertRaises(ValueError): e.tool('worker_1','submit_discussion_statement',dict(text='  '),'discussion_1')
        e.tool('worker_1','submit_discussion_statement',dict(text='Valid'),'discussion_1')
        with self.assertRaises(ValueError): e.tool('worker_1','submit_discussion_statement',dict(text='Duplicate'),'discussion_1')
        e.prepare_phase('ballot',e.workers)
        with self.assertRaises(ValueError): e.tool('worker_1','submit_ballot',dict(candidate_id='manager'),'ballot')
        with self.assertRaises(ValueError): e.tool('worker_1','submit_private_message',dict(recipient='worker_2',text='No'),'ballot')
        e.tool('worker_1','submit_ballot',dict(candidate_id='worker_1'),'ballot')
        with self.assertRaises(ValueError): e.tool('worker_1','submit_ballot',dict(candidate_id='worker_2'),'ballot')

    @patch('swarm.election.mlflow.log_metrics')
    def test_failures_abstentions_and_missing_plans(self,metrics):
        self.provider=ElectionScript(missing_statement='worker_2',missing_vote='worker_3',missing_plan='manager',late_failure='worker_1')
        e=self.make();e.run()
        self.assertEqual(e.e['discussion_rounds'][0]['missing_statements'],['worker_2'])
        self.assertIn('worker_1',e.e['ballots'])
        self.assertEqual(e.e['result']['valid_votes'],4)
        self.assertEqual(e.e['result']['abstentions'],1)
        self.assertEqual(e.e['abstentions'][0]['voter'],'worker_3')
        self.assertIn('manager',e.e['future_plan_failures'])
        self.assertFalse(e.e['future_planning_complete'])
        self.assertEqual(self.state.data['status'],'completed')

    @patch('swarm.election.mlflow.log_metrics')
    def test_private_messages_off(self,metrics):
        self.config['private_messages']=False
        e=self.make();e.prepare_phase('discussion_1',e.workers,1)
        with self.assertRaises(ValueError): e.tool('worker_1','submit_private_message',dict(recipient='worker_2',text='No'),'discussion_1')
        # Recreate only the in-memory fixture so run owns all phase boundaries.
        e.run()
        self.assertEqual(self.state.data['private'],[])
        self.assertTrue(all('submit_private_message' not in names for names in self.provider.toolsets))

    @patch('swarm.election.mlflow.log_metrics')
    def test_everyone_abstains_but_all_agents_plan(self,metrics):
        class NoVotes(ElectionScript):
            def chat(self,*args):
                ctx=json.loads(args[2][1]['content'])
                if ctx['phase']=='ballot': raise ValueError('No vote')
                return super().chat(*args)
        self.provider=NoVotes();e=self.make();e.run()
        self.assertEqual(self.state.data['status'],'election_incomplete')
        self.assertIsNone(e.e['result']['winner'])
        self.assertEqual(e.e['result']['abstentions'],5)
        self.assertEqual(len(e.e['future_plans']),6)

    def test_infrastructure_failure_stops(self):
        e=self.make();e.prepare_phase('discussion_1',e.workers,1)
        async def fail(*args): raise LoggingError('Logging unavailable')
        e.aturn=fail
        with self.assertRaises(LoggingError): e.attempt('worker_1','discussion_1')
        self.assertEqual(e.e['ballots'],{})

    @patch('swarm.controller.mlflow.log_metrics')
    @patch('swarm.election.mlflow.log_metrics')
    def test_full_five_worker_pipeline_has_45_turns(self,election_metrics,work_metrics):
        self.state.data.update(status='created',round=0,completed_rounds=0)
        issues=[dict(id=f'I{i}',priority='high',observed='Symptom',expected='Expected') for i in range(100)]
        work=Controller(self.state,self.config,Workspace(),self.provider,self.journal,issues)
        work.run();self.assertEqual(self.state.data['status'],'election_pending')
        self.make().run()
        self.assertEqual(len(self.state.data['turns']),45)
        self.assertEqual(self.state.data['status'],'completed')

    def test_seed_replay_and_no_false_winner(self):
        workers=['worker_1','worker_2','worker_3']
        ballots={w:dict(candidate_id=w) for w in workers}
        self.assertEqual(tally_votes(workers,ballots,42),tally_votes(list(reversed(workers)),ballots,42))
        self.assertTrue(tally_votes(workers,ballots,42)['tie_break_used'])
        self.assertIsNone(tally_votes(workers,{},42)['winner'])
        with self.assertRaises(ValueError): tally_votes(workers,{'manager':dict(candidate_id='worker_1')},42)

    def test_election_cli_defaults_and_override(self):
        from swarm.cli import parser
        with patch.dict('os.environ',{},clear=True):
            self.assertTrue(parser().parse_args(['run']).election)
            self.assertEqual(parser().parse_args(['run']).election_seed,42)
            self.assertFalse(parser().parse_args(['run','--election','false']).election)
        with patch.dict('os.environ',{'SWARM_ELECTION_ENABLED':'false','SWARM_ELECTION_SEED':'7'}):
            self.assertFalse(parser().parse_args(['run']).election)
            self.assertTrue(parser().parse_args(['run','--election','true']).election)
            self.assertEqual(parser().parse_args(['run']).election_seed,7)

    @patch('swarm.controller.mlflow.log_metrics')
    @patch('swarm.election.mlflow.log_metrics')
    def test_cli_full_pipeline_and_work_only_mode(self,election_metrics,work_metrics):
        from swarm.cli import main
        for enabled in [False,True]:
            root=Path(self.temp.name)/('full' if enabled else 'work-only')
            script=ElectionScript()
            script.check=lambda *args: None
            journals=[]
            def telemetry(state,config):
                journal=Journal(state);journal.run_id='test-only';journal.finish=lambda *args,**kwargs: None
                journals.append(journal)
                return journal
            with patch.dict('os.environ',{'SWARM_DATA':str(root),'OLLAMA_API_KEY':'test-key-not-real'},clear=True), \
                 patch('sys.argv',['swarm','run','--run-id','cli-test','--election',str(enabled).lower()]), \
                 patch('swarm.cli.Workspace',return_value=Workspace()), \
                 patch('swarm.cli.Ollama',return_value=script), \
                 patch('swarm.cli.Telemetry',side_effect=telemetry):
                main()
            state=journals[0].state.data
            self.assertEqual(state['status'],'completed' if enabled else 'election_pending')
            self.assertEqual(len(state['turns']),45 if enabled else 19)
            self.assertEqual('election' in state,enabled)
