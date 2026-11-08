import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import mlflow
from mlflow import MlflowClient

from swarm.cli import parser, main
from swarm.controller import Controller
from swarm.election import Election
from swarm.election_audit import audit_election
from swarm.parallel import run_stage
from swarm.parallel_audit import audit_parallel
from swarm.provider import Ollama, ProviderError
from swarm.state import State
from swarm.telemetry import LoggingError
from test_controller import Journal, Workspace, Scripted
from test_election import ElectionScript


class Delayed(ElectionScript):
    def __init__(self):
        super().__init__();self.active=0;self.maximum=0;self.releases={}

    async def achat(self,model,thinking,messages,available,config):
        ctx=json.loads(messages[1]['content'])
        self.active+=1;self.maximum=max(self.maximum,self.active)
        try:
            n=int(ctx['agent'].split('_')[-1]) if ctx['agent']!='manager' else 0
            if ctx['phase']=='work' and config['concurrency']>1 and ctx['agent']=='worker_1':
                await self.releases.setdefault(ctx['round'],asyncio.Event()).wait()
            await asyncio.sleep(.001*(7-n))
            if ctx['phase']=='work' and config['private_messages'] and not any(m['role']=='assistant' for m in messages):
                recipient='worker_'+str(n%5+1)
                return dict(message=dict(role='assistant',content='',tool_calls=[dict(function=dict(
                    name='submit_private_message',arguments=dict(recipient=recipient,text='work secret '+ctx['agent'])))]))
            # The added private-message call must not advance the underlying script.
            clean=[m for m in messages if not (m['role']=='tool' and m.get('tool_name')=='submit_private_message')]
            if ctx['phase']=='work':
                clean=[m for m in clean if not (m['role']=='assistant' and any(c['function']['name']=='submit_private_message' for c in m.get('tool_calls',[])))]
            response=self.chat(model,thinking,clean,available,config)
            if ctx['phase']=='work' and ctx['agent']=='worker_5' and not response['message'].get('tool_calls'):
                self.releases.setdefault(ctx['round'],asyncio.Event()).set()
            return response
        finally:
            self.active-=1


class ParallelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=State(self.temp.name);self.provider=Delayed();self.workspace=Workspace()
        self.config=dict(workers=5,rounds=3,concurrency=5,election=True,election_seed=42,
            private_messages=True,public_messages=True,manager_model='fake',worker_model='fake',
            manager_thinking=True,worker_thinking=True,worker_call_limit=12,manager_call_limit=12)
        self.journal=Journal(self.state)
        self.controller=Controller(self.state,self.config,self.workspace,self.provider,self.journal,
            [dict(id=f'I{i}',priority='high',observed='symptom',expected='expected') for i in range(100)])

    def test_concurrency_configuration_and_validation(self):
        with patch.dict('os.environ',{},clear=True):
            self.assertEqual(parser().parse_args(['run']).concurrency,5)
        with patch.dict('os.environ',dict(SWARM_CONCURRENCY='3')):
            self.assertEqual(parser().parse_args(['run']).concurrency,3)
            self.assertEqual(parser().parse_args(['run','--concurrency','2']).concurrency,2)
        for value in ['0','-1']:
            with patch.dict('os.environ',dict(SWARM_DATA=self.temp.name)), patch('sys.argv',['swarm','run','--concurrency',value]):
                with self.assertRaises(ValueError): main()

    def events(self):
        return [dict(id=r['id'],kind=r['kind'],body=json.loads(r['body'])) for r in self.state.db.execute('SELECT * FROM events ORDER BY id')]

    @patch('swarm.parallel.mlflow.log_metrics')
    async def test_full_parallel_pipeline_barriers_visibility_and_order(self,metrics):
        await self.controller.arun()
        await Election(self.state,self.config,self.provider,self.journal).arun()
        s=self.state.data;events=self.events()
        self.assertEqual(s['status'],'completed');self.assertEqual(len(s['turns']),45)
        self.assertEqual(self.provider.maximum,5)
        self.assertTrue(audit_election(s,events)['collective_reveals_verified'])
        stage_ends=[e['body'] for e in events if e['kind']=='parallel_stage_finished']
        self.assertEqual(len(stage_ends),8)
        self.assertTrue(all(b['max_active_turns']==5 for b in stage_ends))
        for round in range(1,4):
            contexts=[e['body']['context'] for e in events if e['kind']=='turn_start' and e['body']['phase']=='work' and e['body']['round']==round]
            self.assertEqual(len(contexts),5)
            self.assertTrue(all(c['public_channel']==contexts[0]['public_channel'] for c in contexts))
            self.assertTrue(all(not any(m['round']==round for m in c['private_inbox']) for c in contexts))
            if round>1: self.assertTrue(all(c['private_inbox'] for c in contexts))
            # All worker messages are delivered at the barrier in configured sender order.
            delivered=next(e['body']['messages'] for e in events if e['kind']=='work_messages_delivered' and e['body']['round']==round)
            self.assertEqual([m['message']['sender'] for m in delivered],sorted([m['message']['sender'] for m in delivered]))
            review=next(e for e in events if e['kind']=='turn_start' and e['body']['phase']=='review' and e['body']['round']==round)
            finished=[e for e in events if e['kind']=='turn_end' and e['body']['phase']=='work' and e['body']['round']==round]
            self.assertGreater(review['id'],max(e['id'] for e in finished))
            self.assertEqual([p['worker'] for p in review['body']['context']['submissions']],self.controller.workers)
        completed=[e['body']['agent'] for e in events if e['kind']=='turn_end' and e['body']['phase']=='work' and e['body']['round']==1]
        self.assertNotEqual(completed,self.controller.workers)
        self.assertEqual(completed[-1],'worker_1')
        self.assertEqual(s['pending_work_messages'],[])
        self.assertTrue(audit_parallel(s,events)['parallel_barriers_verified'])
        tampered=copy.deepcopy(events)
        end=next(e for e in tampered if e['kind']=='parallel_stage_finished')
        end['body']['participants']=[]
        with self.assertRaises(AssertionError): audit_parallel(s,tampered)

    @patch('swarm.parallel.mlflow.log_metrics')
    async def test_one_slot_and_waiting_workers_use_frozen_context(self,metrics):
        self.config.update(concurrency=1,rounds=1)
        await self.controller.arun()
        self.assertEqual(self.provider.maximum,1)
        contexts=[e['body']['context'] for e in self.events() if e['kind']=='turn_start' and e['body']['phase']=='work']
        self.assertTrue(all(c['public_channel']==contexts[0]['public_channel'] for c in contexts))
        self.assertTrue(all(c['private_inbox']==[] for c in contexts))
        self.assertEqual(len(self.state.data['private']),5)

    @patch('swarm.parallel.mlflow.log_metrics')
    async def test_failed_agent_releases_slot_and_next_stage_waits(self,metrics):
        self.config.update(concurrency=2,rounds=1)
        original=self.provider.achat
        async def failing(model,thinking,messages,available,config):
            ctx=json.loads(messages[1]['content'])
            if ctx['agent']=='worker_2': raise ProviderError('Unavailable')
            return await original(model,thinking,messages,available,config)
        self.provider.achat=failing
        await self.controller.arun()
        self.assertEqual(self.state.data['status'],'election_pending')
        self.assertEqual(len(self.state.data['submissions']),4)
        self.assertEqual(self.state.board(['worker_2'])[0]['average_rating'],None)
        self.assertEqual(self.provider.active,0)

    @patch('swarm.parallel.mlflow.log_metrics')
    async def test_multiple_accepted_patches_apply_in_worker_order(self,metrics):
        self.config['rounds']=1
        original=self.provider.chat
        def accept_all(model,thinking,messages,available,config):
            ctx=json.loads(messages[1]['content'])
            if ctx['agent']=='manager' and ctx['phase']=='review':
                stage=sum(m['role']=='assistant' for m in messages)
                if stage<len(ctx['submissions']):
                    return dict(message=dict(role='assistant',content='',tool_calls=[dict(function=dict(
                        name='accept_patch',arguments=dict(submission_id=ctx['submissions'][stage]['id'],rating=4,feedback='Focused')))]))
                return dict(message=dict(role='assistant',content=json.dumps(dict(scratchpad='notes',assignments=[],rejections=[]))))
            response=original(model,thinking,messages,available,config)
            for call in response['message'].get('tool_calls',[]):
                if call['function']['name']=='submit_patch':
                    args=call['function']['arguments']
                    args['diff']=args['diff'].replace('+value = 2','+value = 2 # '+ctx['agent'])
            return response
        self.provider.chat=accept_all
        await self.controller.arun()
        self.assertEqual(len(self.workspace.applications),5)
        for worker,diff in zip(self.controller.workers,self.workspace.applications):
            self.assertIn('# '+worker,diff)
        self.assertEqual([s['worker'] for s in self.state.data['submissions']][-1],'worker_1')

    @patch('swarm.parallel.mlflow.log_metrics')
    async def test_parallel_work_with_both_message_tools_disabled(self,metrics):
        self.config.update(rounds=1,concurrency=2,private_messages=False,public_messages=False)
        await self.controller.arun()
        self.assertEqual(len(self.state.data['submissions']),5)
        self.assertEqual(self.state.data['private'],[])
        self.assertFalse(any(e['kind']=='work_message_queued' for e in self.events()))
        for names in self.provider.work.toolsets:
            self.assertNotIn('submit_private_message',names)
            self.assertNotIn('submit_public_message',names)

    @patch('swarm.parallel.mlflow.log_metrics')
    async def test_fatal_logging_failure_cancels_requests_and_stops_queue(self,metrics):
        self.config['concurrency']=2
        started=[];cancelled=[]
        async def attempt(agent,phase):
            started.append(agent)
            if agent=='worker_1':
                await asyncio.sleep(.01)
                self.state.event('pending_upload',dict(agent=agent))
                raise LoggingError('Upload failed')
            try: await asyncio.sleep(20)
            except asyncio.CancelledError:
                cancelled.append(agent);raise
        with self.assertRaises(LoggingError):
            await run_stage(self.controller,'work',self.controller.workers,attempt)
        self.assertEqual(started,['worker_1','worker_2'])
        self.assertEqual(cancelled,['worker_2'])
        self.assertFalse(any(e['kind']=='parallel_stage_finished' for e in self.events()))
        self.assertGreater(self.state.db.execute('SELECT count(*) FROM events WHERE logged=0').fetchone()[0],0)

    async def test_native_async_requests_overlap_and_have_agent_identity(self):
        provider=Ollama('https://example.test','fake-key',self.journal)
        active=0;maximum=0
        async def request(req):
            nonlocal active,maximum
            active+=1;maximum=max(maximum,active)
            try:
                await asyncio.sleep(.01)
                return httpx.Response(200,json=dict(message=dict(role='assistant',thinking='test',content='{}')))
            finally: active-=1
        provider.async_transport=httpx.MockTransport(request)
        async def chat(agent):
            ctx=dict(agent=agent,phase='work',round=1)
            return await provider.achat('fake',True,[dict(role='system',content='s'),dict(role='user',content=json.dumps(ctx))],[],dict(temperature=.7,max_tokens=100))
        await asyncio.gather(*(chat(w) for w in self.controller.workers))
        self.assertEqual(maximum,5)
        self.assertEqual({e['body']['agent'] for e in self.events() if e['kind']=='model_response'},set(self.controller.workers))

    async def test_mlflow_spans_remain_isolated_across_async_turns(self):
        uri='sqlite:///'+str(Path(self.temp.name)/'tracking.db')
        previous=mlflow.get_tracking_uri();mlflow.set_tracking_uri(uri)
        try:
            mlflow.set_experiment('parallel-scripted-test')
            with mlflow.start_run() as run:
                async def agent(name):
                    with mlflow.start_span(name,span_type='AGENT') as parent:
                        with mlflow.start_span(name+'_model',span_type='LLM'):
                            await asyncio.sleep(.01)
                        return parent.trace_id
                ids=await asyncio.gather(*(agent(w) for w in self.controller.workers))
                mlflow.flush_trace_async_logging()
                client=MlflowClient(tracking_uri=uri)
                self.assertEqual(len(set(ids)),5)
                for name,trace_id in zip(self.controller.workers,ids):
                    trace=client.get_trace(trace_id)
                    self.assertEqual(trace.info.request_metadata['mlflow.sourceRun'],run.info.run_id)
                    spans=trace.data.spans
                    self.assertEqual({s.name for s in spans},{name,name+'_model'})
                    root=next(s for s in spans if s.name==name)
                    child=next(s for s in spans if s.name==name+'_model')
                    self.assertEqual(child.parent_id,root.span_id)
        finally: mlflow.set_tracking_uri(previous)
