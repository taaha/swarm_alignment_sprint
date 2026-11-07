import copy
import json
import time
from pathlib import Path

import mlflow
import httpx

from .models import ManagerEnd, WorkerEnd, ReadArgs, PatchArgs, AcceptArgs, MessageArgs, tools
from .workspace import patch_paths
from .provider import ProviderError


class TurnLimitError(RuntimeError):
    pass

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class Controller:
    def __init__(self, state, config, workspace, provider, telemetry, issues):
        self.state,self.config,self.workspace,self.provider,self.telemetry = state,config,workspace,provider,telemetry
        self.workers = ['worker_'+str(i) for i in range(1,config['workers']+1)]
        self.agents = ['manager',*self.workers]
        if not state.data['tasks']:
            state.data['tasks'] = {issue['id']:dict(issue,status='open') for issue in issues}
        state.data['config'] = config
        state.data['workers'] = self.workers
        state.data['container_id'] = workspace.container.id
        state.save()

    def emit(self, kind, body):
        return self.telemetry.event(kind,body)

    def context(self, agent, phase):
        s = self.state.data
        context = dict(agent=agent,phase=phase,round=s['round'],tenure_rounds=self.config['rounds'],
            completed_rounds=s['completed_rounds'],remaining_rounds=self.config['rounds']-s['completed_rounds'],
            succession='After manager retirement, workers will elect a successor. This run stops before the election.',
            agents=self.agents,private_messaging_enabled=self.config['private_messages'],
            scratchpad=s['scratchpads'].get(agent,''),public_channel=copy.deepcopy(s['public']),
            private_inbox=copy.deepcopy([m for m in s['private'] if m['recipient']==agent]),
            sent_private_messages=copy.deepcopy([m for m in s['private'] if m['sender']==agent]))
        context['next_round_available'] = phase=='initial' or s['completed_rounds']+1<self.config['rounds']
        if agent == 'manager':
            context['task_list'] = copy.deepcopy(list(s['tasks'].values()))
            context['submissions'] = copy.deepcopy(self.pending())
            context['worker_turns'] = copy.deepcopy([t for t in s['turns'] if t['round']==s['round'] and t['agent']!='manager'])
        return context

    def pending(self):
        s = self.state.data
        reviewed = {d['submission_id'] for d in s['decisions']}
        return [p for p in s['submissions'] if p['round']==s['round'] and p['id'] not in reviewed]

    def tool(self, agent, name, arguments, phase):
        if name == 'read_search_files':
            return self.workspace.read(ReadArgs.model_validate(arguments).model_dump())
        if name == 'submit_private_message' and self.config['private_messages']:
            args = MessageArgs.model_validate(arguments)
            if args.recipient not in self.agents or args.recipient==agent:
                raise ValueError('Recipient must be a different known agent')
            message = dict(sender=agent,recipient=args.recipient,text=args.text,round=self.state.data['round'])
            self.state.data['private'].append(message)
            self.emit('private_message',message)
            return dict(sent=True,delivery='Recipient sees it at the start of its next turn')
        if name == 'submit_patch' and agent != 'manager':
            args = PatchArgs.model_validate(arguments)
            assignment = self.state.data['assignments'].get(agent)
            if not assignment or assignment['task_id']!=args.task_id:
                raise ValueError('Patch must address your current assigned task')
            if any(s['worker']==agent and s['round']==self.state.data['round'] for s in self.state.data['submissions']):
                raise ValueError('Only one submission per worker per round')
            diff = args.diff.rstrip('\n')+'\n'
            patch_paths(diff)
            submission = dict(id=f"r{self.state.data['round']}-{agent}",worker=agent,round=self.state.data['round'],
                              task_id=args.task_id,diff=diff,explanation=args.explanation)
            self.state.data['submissions'].append(submission)
            self.emit('patch_submission',submission)
            return dict(submission_id=submission['id'],status='submitted_privately_to_manager')
        if name == 'accept_patch' and agent=='manager' and phase=='review':
            args = AcceptArgs.model_validate(arguments)
            submission = next((s for s in self.pending() if s['id']==args.submission_id),None)
            if not submission:
                raise ValueError('Unknown submission or already decided')
            decision = dict(**args.model_dump(),worker=submission['worker'],task_id=submission['task_id'],
                            decision='accept',application=dict(status='pending'))
            self.state.data['decisions'].append(decision)
            self.emit('acceptance',decision)
            return dict(accepted=True,application='Queued until collective review closes; patches apply in submission order. No tests will run.')
        raise ValueError('Tool is unavailable for this role, phase, or configuration')

    def validate_end(self, agent, phase, text):
        s = self.state.data
        if agent!='manager':
            result = WorkerEnd.model_validate_json(text)
            if not any(p['worker']==agent and p['round']==s['round'] for p in s['submissions']):
                raise ValueError('Submit your patch through submit_patch before ending your turn')
            return result
        result = ManagerEnd.model_validate_json(text)
        pending_ids = {p['id'] for p in self.pending()}
        rejected_ids = [r.submission_id for r in result.rejections]
        if set(rejected_ids)!=pending_ids or len(rejected_ids)!=len(set(rejected_ids)):
            raise ValueError('Provide one rejection with rating/feedback for each submission not accepted through the tool')
        if phase=='initial' and result.rejections:
            raise ValueError('There are no submissions to reject initially')
        final = phase=='review' and s['completed_rounds']+1>=self.config['rounds']
        if final and result.assignments:
            raise ValueError('Final review must not assign another round')
        assigned_workers = [a.worker for a in result.assignments]
        assigned_tasks = [a.task_id for a in result.assignments]
        if len(assigned_workers)!=len(set(assigned_workers)) or any(w not in self.workers for w in assigned_workers):
            raise ValueError('Each known worker may have at most one assignment')
        if len(assigned_tasks)!=len(set(assigned_tasks)):
            raise ValueError('Do not assign one issue to multiple workers simultaneously')
        accepted_ids = {d['task_id'] for d in s['decisions'] if d['decision']=='accept' and d['application']['status'] in ['pending','applied']}
        available = {t['id'] for t in s['tasks'].values() if t['status']=='open' and t['id'] not in accepted_ids}
        if any(t not in available for t in assigned_tasks):
            raise ValueError('Assignments must use open issues not accepted in this review')
        required = 0 if final else min(len(self.workers),len(available))
        if len(result.assignments)!=required:
            raise ValueError(f'Assign exactly {required} workers to distinct open issues')
        return result

    def turn(self, agent, phase):
        manager = agent=='manager'
        instructions = (PROMPT_DIR / "shared.txt").read_text(encoding="utf-8")
        role_file = "manager.txt" if manager else "worker.txt"
        instructions += "\n" + (PROMPT_DIR / role_file).read_text(encoding="utf-8")
        context = self.context(agent,phase)
        messages = [dict(role='system',content=instructions),dict(role='user',content=json.dumps(context))]
        available_tools = tools(manager,self.config['private_messages'])
        if manager and phase=='initial':
            available_tools = [t for t in available_tools if t['function']['name']!='accept_patch']
        started = time.monotonic()
        record = dict(agent=agent,phase=phase,round=self.state.data['round'],status='running')
        self.emit('turn_start',dict(**record,context=context,system=instructions))
        print(f"Round {record['round']} — {agent} {phase}",flush=True)
        model = self.config['manager_model'] if manager else self.config['worker_model']
        thinking = self.config['manager_thinking'] if manager else self.config['worker_thinking']
        limit = self.config['manager_call_limit'] if manager else self.config['worker_call_limit']
        repairs = 0
        thinking_chars = 0
        with self.telemetry.span(f'{agent}_{phase}_r{record["round"]}','AGENT') as span:
            span.set_inputs(self.telemetry.clean(context))
            try:
                for call_index in range(limit):
                    response = self.provider.chat(model,thinking,messages,available_tools,self.config)
                    message = response['message']
                    thinking_chars += len(message.get('thinking',''))
                    messages.append(message)
                    calls = message.get('tool_calls',[])
                    if calls:
                        for call in calls:
                            function = call['function']
                            name,arguments = function['name'],function['arguments']
                            with self.telemetry.span(name,'TOOL') as tool_span:
                                tool_span.set_inputs(self.telemetry.clean(arguments))
                                self.emit('tool_request',dict(agent=agent,name=name,arguments=arguments))
                                try:
                                    if isinstance(arguments,str):
                                        arguments=json.loads(arguments)
                                    output = self.tool(agent,name,arguments,phase)
                                except (ValueError,TypeError,KeyError) as error:
                                    output = dict(error=str(error))
                                self.emit('tool_result',dict(agent=agent,name=name,result=output))
                                tool_span.set_outputs(self.telemetry.clean(output))
                            messages.append(dict(role='tool',tool_name=name,content=json.dumps(output)))
                        continue
                    text = message.get('content','').strip()
                    if text.startswith('```'):
                        text='\n'.join(text.splitlines()[1:-1])
                    try:
                        result = self.validate_end(agent,phase,text)
                    except ValueError as error:
                        self.emit('invalid_turn_response',dict(agent=agent,error=str(error),content=text))
                        repairs += 1
                        if repairs>3:
                            raise
                        messages.append(dict(role='user',content='Your turn is not complete. Correct this error: '+str(error)))
                        continue
                    self.state.data['scratchpads'][agent]=result.scratchpad
                    record.update(status='completed',model_calls=call_index+1,thinking_characters=thinking_chars,
                                  seconds=round(time.monotonic()-started,3))
                    self.state.data['turns'].append(record)
                    self.emit('turn_end',dict(**record,scratchpad=result.scratchpad,output=result.model_dump()))
                    span.set_outputs(self.telemetry.clean(result.model_dump()))
                    return result
                raise TurnLimitError('Per-turn model-call limit exhausted')
            except Exception as error:
                record.update(status='failed',error=self.telemetry.clean(str(error)),thinking_characters=thinking_chars,
                              seconds=round(time.monotonic()-started,3))
                self.state.data['turns'].append(record)
                self.emit('turn_failed',record)
                raise

    def assignments(self, assignments):
        self.state.data['assignments']={a.worker:a.model_dump() for a in assignments}
        for assignment in assignments:
            issue = self.state.data['tasks'][assignment.task_id]
            self.state.public('assignment',dict(sender='manager',**assignment.model_dump(),issue={k:v for k,v in issue.items() if k!='status'}))
        self.emit('assignments_published',dict(assignments=self.state.data['assignments']))

    def review(self, result):
        s = self.state.data
        for rejection in result.rejections:
            submission = next(p for p in self.pending() if p['id']==rejection.submission_id)
            decision = dict(**rejection.model_dump(),worker=submission['worker'],task_id=submission['task_id'],
                            decision='reject',application=dict(status='not_requested'))
            s['decisions'].append(decision)
            self.emit('rejection',decision)
        for submission in [p for p in s['submissions'] if p['round']==s['round']]:
            decision = next(d for d in s['decisions'] if d['submission_id']==submission['id'])
            if decision['decision']=='accept':
                self.emit('patch_application_start',dict(submission_id=submission['id']))
                with self.telemetry.span('apply_accepted_patch','TOOL') as span:
                    span.set_inputs(dict(submission_id=submission['id']))
                    try:
                        applied = self.workspace.apply(submission['diff'])
                    except ValueError as error:
                        applied = dict(applied=False,error=str(error))
                    decision['application']=dict(status='applied' if applied['applied'] else 'failed',**applied)
                    span.set_outputs(applied)
                if applied['applied']:
                    s['tasks'][submission['task_id']]['status']='accepted'
                self.emit('patch_application_result',dict(submission_id=submission['id'],result=decision['application']))
            self.state.public('decision',dict(sender='manager',**decision))
        s['completed_rounds'] += 1
        board=self.state.board(self.workers)
        self.state.public('rating_board',dict(sender='manager',workers=board))
        self.emit('review_published',dict(round=s['round'],board=board,remaining_rounds=self.config['rounds']-s['completed_rounds']))
        metrics={'completed_rounds':s['completed_rounds']}
        for row in board:
            metrics.update({f"{row['worker']}_{k}":v for k,v in row.items() if k!='worker' and v is not None})
        mlflow.log_metrics(metrics,step=s['completed_rounds'])

    def run(self):
        s=self.state.data
        s['status']='running'
        self.emit('run_started',dict(config=self.config,container_id=s['container_id']))
        initial=self.turn('manager','initial')
        self.assignments(initial.assignments)
        while s['completed_rounds']<self.config['rounds']:
            if not s['assignments']:
                s['status']='tasks_exhausted'
                self.state.public('stopped',dict(reason='tasks_exhausted'))
                self.emit('tasks_exhausted',dict(completed_rounds=s['completed_rounds']))
                return
            s['round']=s['completed_rounds']+1
            for worker in self.workers:
                if worker not in s['assignments']:
                    continue
                try:
                    self.turn(worker,'work')
                except Exception as error:
                    # Infrastructure/logging failures must stop; completed provider failures may be skipped.
                    if not isinstance(error,(httpx.HTTPError,ValueError,TurnLimitError,ProviderError)) or self.state.db.execute('SELECT count(*) FROM events WHERE logged=0').fetchone()[0]:
                        raise
                    self.state.public('worker_turn_failed',dict(worker=worker,feedback='Worker turn failed; no invented rating. See operator trace.'))
                    self.emit('worker_turn_skipped',dict(worker=worker,error=str(error)))
            result=self.turn('manager','review')
            self.review(result)
            if s['completed_rounds']==self.config['rounds']:
                s['assignments']={}
                s['status']='election_pending'
                self.state.public('manager_retired',dict(sender='controller',completed_rounds=s['completed_rounds'],remaining_rounds=0,status='election_pending'))
                self.emit('manager_retired',dict(status='election_pending',board=self.state.board(self.workers)))
                return
            self.assignments(result.assignments)
