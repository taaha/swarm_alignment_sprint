import contextlib
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from swarm.controller import Controller
from swarm.models import tools
from swarm.state import State
from swarm.workspace import apply_diff, patch_paths
from swarm import remote


class Span:
    def set_inputs(self, value): pass
    def set_outputs(self, value): pass
    def set_attribute(self, name, value): pass


class Journal:
    def __init__(self,state): self.state=state
    def clean(self,value): return value
    def event(self,kind,body):
        index=self.state.event(kind,body)
        self.state.db.execute('UPDATE events SET logged=1 WHERE id=?',(index,))
        self.state.db.commit()
        return index
    def span(self,*args): return contextlib.nullcontext(Span())
    def usage(self,*args): pass


class Workspace:
    container=types.SimpleNamespace(id='working-container')
    def __init__(self): self.applications=[]
    def read(self,args): return dict(lines=['1: value = 1'],total_lines=1)
    def apply(self,diff):
        self.applications.append(diff)
        return dict(applied=True,files=['src/oscar/example.py'])


class Scripted:
    def __init__(self): self.contexts=[]; self.toolsets=[]
    def chat(self,model,thinking,messages,available,config):
        context=json.loads(messages[1]['content'])
        self.contexts.append(copy.deepcopy(context))
        self.toolsets.append([t['function']['name'] for t in available])
        agent=context['agent']
        stage=sum(m['role']=='assistant' for m in messages)
        def call(name,args):
            return dict(message=dict(role='assistant',thinking='test reasoning',content='',tool_calls=[dict(function=dict(name=name,arguments=args))]))
        if agent!='manager':
            if stage==0:
                return call('read_search_files',dict(operation='read',path='src/oscar/example.py'))
            if stage==1 and config.get('public_messages',False):
                return call('submit_public_message',dict(text='Investigation complete; submitting a focused proposal.'))
            if stage==(2 if config.get('public_messages',False) else 1):
                task=next(m['task_id'] for m in reversed(context['public_channel']) if m['kind']=='assignment' and m['worker']==agent)
                return call('submit_patch',dict(task_id=task,diff='--- a/src/oscar/example.py\n+++ b/src/oscar/example.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n',explanation='Correct the reported behavior'))
            return dict(message=dict(role='assistant',thinking='done',content=json.dumps(dict(scratchpad='private '+agent))))
        submissions=context.get('submissions',[])
        if context['phase']=='review' and stage==0 and submissions:
            return call('accept_patch',dict(submission_id=submissions[0]['id'],rating=4,feedback='Focused patch'))
        rejections=[dict(submission_id=p['id'],rating=2,feedback='Needs revision') for p in submissions[1:]]
        closed={m['task_id'] for m in context['public_channel'] if m['kind']=='decision' and m['decision']=='accept' and m['application']['status']=='applied'}
        if submissions: closed.add(submissions[0]['task_id'])
        available_tasks=[t for t in context['task_list'] if t['id'] not in closed]
        assignments=[]
        if context['next_round_available']:
            assignments=[dict(worker=worker,task_id=task['id'],instructions='Investigate') for worker,task in zip(context['agents'][1:],available_tasks)]
        return dict(message=dict(role='assistant',thinking='review',content=json.dumps(dict(scratchpad='manager notes',rejections=rejections,assignments=assignments))))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state=State(self.temp.name)
        self.workspace=Workspace()
        self.provider=Scripted()
        self.config=dict(workers=5,rounds=3,private_messages=True,public_messages=True,manager_model='fake',worker_model='fake',
                         manager_thinking='high',worker_thinking='high',worker_call_limit=10,manager_call_limit=10)
        self.issues=[dict(id=f'I{i}',priority='high',observed='Symptom',expected='Expected') for i in range(100)]
        self.controller=Controller(self.state,self.config,self.workspace,self.provider,Journal(self.state),self.issues)

    @patch('swarm.controller.mlflow.log_metrics')
    def test_full_five_worker_three_round_workflow(self,metrics):
        self.controller.run()
        s=self.state.data
        self.assertEqual(s['status'],'election_pending')
        self.assertEqual(s['completed_rounds'],3)
        self.assertEqual(len(s['submissions']),15)
        self.assertEqual(len(s['decisions']),15)
        self.assertEqual(len([m for m in s['public'] if m['kind']=='worker_message']),15)
        self.assertEqual(len(s['turns']),19)
        self.assertEqual(len(self.workspace.applications),3)
        self.assertEqual(s['assignments'],{})
        self.assertEqual(s['public'][-1]['kind'],'manager_retired')
        for round in range(1,4):
            turns=[t['agent'] for t in s['turns'] if t['round']==round]
            self.assertEqual(turns,[*self.controller.workers,'manager'])
        board=self.state.board(self.controller.workers)
        self.assertEqual(board[0]['accepted'],3)
        self.assertEqual(board[0]['average_rating'],4)
        self.assertEqual(board[1]['rejected'],3)
        for context in self.provider.contexts:
            if context['agent']!='manager':
                self.assertNotIn('submissions',context)
                self.assertNotIn('task_list',context)
            for message in context['public_channel']:
                self.assertNotIn('diff',message)
                self.assertNotIn('scratchpad',message)
        self.assertEqual(State(self.temp.name).data['status'],'election_pending')

    def test_public_post_persistence_visibility_and_sender(self):
        c=self.controller
        first_context=c.context('worker_1','work')
        self.state.data['round']=1
        result=c.tool('worker_1','submit_public_message',dict(text='  Investigating payment amounts.  '),'work')
        expected=dict(kind='worker_message',round=1,sender='worker_1',text='Investigating payment amounts.')
        self.assertEqual(result['message'],expected)
        self.assertEqual(first_context['public_channel'],[])
        self.assertEqual(c.context('worker_2','work')['public_channel'],[expected])
        self.assertEqual(c.context('manager','review')['public_channel'],[expected])
        self.assertEqual(State(self.temp.name).data['public'],[expected])
        event=self.state.db.execute("SELECT body FROM events WHERE kind='public_message'").fetchone()
        self.assertEqual(json.loads(event['body']),expected)

    def test_public_tool_is_worker_only_and_independent_of_private_messages(self):
        self.controller.config['private_messages']=False
        worker_tools=[t['function']['name'] for t in tools(False,False,True)]
        self.assertIn('submit_public_message',worker_tools)
        self.assertNotIn('submit_private_message',worker_tools)
        self.assertNotIn('submit_public_message',[t['function']['name'] for t in tools(True,True,True)])
        self.controller.tool('worker_2','submit_public_message',dict(text='Public coordination.'),'work')
        for agent,phase in [('manager','review'),('unknown','work'),('worker_1','election')]:
            with self.assertRaises(ValueError):
                self.controller.tool(agent,'submit_public_message',dict(text='Not permitted.'),phase)

    @patch('swarm.controller.mlflow.log_metrics')
    def test_public_posting_disabled_for_entire_workflow(self,metrics):
        self.config['public_messages']=False
        with self.assertRaises(ValueError):
            self.controller.tool('worker_1','submit_public_message',dict(text='Bypass attempt'),'work')
        self.controller.run()
        s=self.state.data
        self.assertEqual(s['status'],'election_pending')
        self.assertEqual(len(s['submissions']),15)
        self.assertEqual(len([m for m in s['public'] if m['kind']=='worker_message']),0)
        self.assertEqual(len([m for m in s['public'] if m['kind']=='rating_board']),3)
        self.assertEqual(len([m for m in s['public'] if m['kind']=='assignment']),15)
        for context,available in zip(self.provider.contexts,self.provider.toolsets):
            self.assertFalse(context['public_messaging_enabled'])
            self.assertNotIn('submit_public_message',available)

    def test_public_posting_env_default_and_cli_override(self):
        from swarm.cli import parser
        with patch.dict('os.environ',{},clear=True):
            self.assertFalse(parser().parse_args(['run']).public_messages)
        with patch.dict('os.environ',{'SWARM_PUBLIC_MESSAGES':'true'}):
            self.assertTrue(parser().parse_args(['run']).public_messages)
            self.assertFalse(parser().parse_args(['run','--public-messages','false']).public_messages)
        with patch.dict('os.environ',{'SWARM_PUBLIC_MESSAGES':'false'}):
            self.assertFalse(parser().parse_args(['run']).public_messages)
            self.assertTrue(parser().parse_args(['run','--public-messages','true']).public_messages)

    def test_public_post_rejects_spoofed_metadata_blank_text_and_diffs(self):
        for arguments in [dict(text='Hello',sender='manager'),dict(text='Hello',round=20),
                          dict(text='   '),dict(text='x'*6001),dict(text='--- a/src/oscar/example.py\n+++ b/src/oscar/example.py')]:
            with self.assertRaises(ValueError):
                self.controller.tool('worker_1','submit_public_message',arguments,'work')
        self.assertEqual(self.state.data['public'],[])

    def test_inboxes_and_private_message_configuration(self):
        c=self.controller
        c.tool('worker_1','submit_private_message',dict(recipient='worker_2',text='Only you'),'work')
        self.assertEqual(c.context('worker_2','work')['private_inbox'][0]['text'],'Only you')
        self.assertEqual(c.context('worker_3','work')['private_inbox'],[])
        self.assertEqual(c.context('manager','review')['private_inbox'],[])
        self.assertEqual(self.state.data['public'],[])
        c.config['private_messages']=False
        self.assertNotIn('submit_private_message',[t['function']['name'] for t in tools(False,False)])
        with self.assertRaises(ValueError):
            c.tool('worker_1','submit_private_message',dict(recipient='worker_2',text='Forbidden'),'work')

    def test_forbidden_tools_and_paths(self):
        for name in ['accept_patch','run_command','write_file']:
            with self.assertRaises(ValueError): self.controller.tool('worker_1',name,{},'work')
        for path in ['../bugs.md','/etc/passwd','shop/../../bugs.md','.env','src/oscar/.git/config']:
            self.assertFalse(remote.permitted(path))
        with self.assertRaises(ValueError):
            patch_paths('--- a/../../host\n+++ b/../../host\n@@ -1 +1 @@\n-x\n+y\n')

    def test_private_scratchpads(self):
        self.state.data['scratchpads']={'worker_1':'secret notes','manager':'manager secret'}
        self.assertEqual(self.controller.context('worker_1','work')['scratchpad'],'secret notes')
        self.assertEqual(self.controller.context('worker_2','work')['scratchpad'],'')

    def test_duplicate_submissions(self):
        self.state.data['assignments']={'worker_1':dict(task_id='I0')}
        args=dict(task_id='I0',diff='--- a/src/oscar/example.py\n+++ b/src/oscar/example.py\n@@ -1 +1 @@\n-a\n+b\n',explanation='Change')
        self.controller.tool('worker_1','submit_patch',args,'work')
        with self.assertRaises(ValueError): self.controller.tool('worker_1','submit_patch',args,'work')

    @patch('swarm.controller.mlflow.log_metrics')
    def test_application_failure_does_not_become_verified_fix(self,metrics):
        self.workspace.apply=lambda diff:dict(applied=False,error='Context mismatch')
        self.controller.run()
        accepted=[d for d in self.state.data['decisions'] if d['decision']=='accept']
        self.assertTrue(all(d['application']['status']=='failed' for d in accepted))
        self.assertTrue(all(t['status']=='open' for t in self.state.data['tasks'].values()))

    @patch('swarm.controller.mlflow.log_metrics')
    def test_missing_submission_is_not_invented_or_rated(self,metrics):
        original=self.controller.turn
        def turn(agent,phase):
            if agent=='worker_5': raise ValueError('Unavailable')
            return original(agent,phase)
        self.controller.turn=turn
        self.controller.run()
        self.assertEqual(self.state.board(['worker_5'])[0],dict(worker='worker_5',submissions=0,accepted=0,rejected=0,average_rating=None))

    def test_bad_final_response_and_call_limit(self):
        with self.assertRaises(ValueError): self.controller.validate_end('manager','initial','{}')
        self.config['worker_call_limit']=1
        with self.assertRaises(RuntimeError): self.controller.turn('worker_1','work')
        self.assertEqual(self.state.data['turns'][-1]['status'],'failed')


class PatchTests(unittest.TestCase):
    def test_apply_and_conflict(self):
        files={'src/oscar/example.py':'value = 1\n'}
        def writer(updates):
            for update in updates: files[update['path']]=update['text']
            return dict(applied=True)
        diff='--- a/src/oscar/example.py\n+++ b/src/oscar/example.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n'
        self.assertTrue(apply_diff(diff,files.__getitem__,writer)['applied'])
        self.assertEqual(files['src/oscar/example.py'],'value = 2\n')
        self.assertFalse(apply_diff(diff,files.__getitem__,writer)['applied'])

    def test_hidden_extra_targets_and_binary_rejected(self):
        diff='--- a/src/oscar/example.py\n+++ b/src/oscar/example.py\n@@ -1 +1 @@\n-a\n+b\n'
        with self.assertRaises(ValueError): patch_paths(diff+'new file mode 100644\n')
        with self.assertRaises(ValueError): patch_paths(diff+'--- a/.env\n+++ b/.env\n')


if __name__=='__main__': unittest.main()
