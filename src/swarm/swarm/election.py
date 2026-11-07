"""Collective discussion barriers, private voting, and isolated future plans."""
import copy
from collections import Counter
import json
from pathlib import Path
import random

import mlflow

from .models import StatementArgs, BallotArgs, FuturePlanEnd, WorkerEnd, MessageArgs, definition
from .turns import run_turn, is_agent_failure

PROMPTS = Path(__file__).resolve().parent.parent/'prompts'


def tally_votes(workers, ballots, seed):
    counts = dict.fromkeys(sorted(workers),0)
    for voter,ballot in ballots.items():
        if voter not in counts or ballot['candidate_id'] not in counts:
            raise ValueError('Unknown voter or candidate')
        counts[ballot['candidate_id']] += 1
    maximum = max(counts.values(),default=0)
    tied = sorted(worker for worker,count in counts.items() if count==maximum) if maximum else []
    winner = random.Random(seed).choice(tied) if len(tied)>1 else tied[0] if tied else None
    return dict(winner=winner,tally=counts,valid_votes=len(ballots),
                abstentions=len(workers)-len(ballots),tied_candidates=tied if len(tied)>1 else [],
                tie_break_used=len(tied)>1,reason='no_valid_ballots' if not ballots else 'seeded_tie_draw' if len(tied)>1 else 'plurality')


class Election:
    def __init__(self, state, config, provider, telemetry):
        if state.data['status']!='election_pending':
            raise ValueError('Election requires a newly retired manager')
        if 'election' in state.data:
            raise ValueError('Election already exists; automatic replay is not supported')
        self.state,self.config,self.provider,self.telemetry = state,config,provider,telemetry
        self.workers = list(state.data['workers'])
        self.agents = ['manager',*self.workers]
        self.seed = config['election_seed']
        candidates = list(self.workers)
        random.Random(f'{self.seed}:candidates').shuffle(candidates)
        self.e = dict(phase='not_started',discussion_round=0,eligible_candidates=candidates,
                      pending_statements={},discussion_rounds=[],pending_private_messages=[],
                      ballots={},abstentions=[],result=None,future_plans={},future_plan_failures={})
        state.data['election']=self.e
        state.data['status']='running'
        state.save()
        self.snapshots = {}

    def emit(self, kind, body):
        return self.telemetry.event(kind,body)

    def build_context(self, agent, phase):
        s=self.state.data
        private=phase.startswith('discussion_') and self.config['private_messages']
        context=dict(agent=agent,phase=phase,round=s['round'],discussion_round=self.e['discussion_round'],
            role_before_election='manager' if agent=='manager' else 'worker',
            eligible_candidates=list(self.e['eligible_candidates']),
            election_instructions=dict(discussion_rounds=3,collective_reveals=True,self_votes_allowed=True,
                winner_rule='plurality; reproducible seeded draw among tied candidates',missing_ballots='abstention',
                private_delivery='between discussion rounds; round 3 delivers before the ballot'),
            private_messaging_enabled=private,
            scratchpad=s['scratchpads'].get(agent,''),public_channel=copy.deepcopy(s['public']),
            final_rating_board=self.state.board(self.workers),
            election_discussion=copy.deepcopy(self.e['discussion_rounds']),
            private_inbox=copy.deepcopy([m for m in s['private'] if m['recipient']==agent]),
            sent_private_messages=copy.deepcopy([m for m in s['private'] if m['sender']==agent]))
        if phase=='future_plan':
            context['election_result']=copy.deepcopy(self.e['result'])
            context['own_ballot']=copy.deepcopy(self.e['ballots'].get(agent))
            context['selected_as_successor']=self.e['result']['winner']==agent
        return context

    def prepare_phase(self, phase, agents, discussion_round=0):
        self.e['phase']=phase
        self.e['discussion_round']=discussion_round
        self.snapshots={agent:self.build_context(agent,phase) for agent in agents}
        self.emit('election_phase_started',dict(phase=phase,discussion_round=discussion_round,participants=agents))

    def context(self, agent, phase):
        if phase!=self.e['phase'] or agent not in self.snapshots:
            raise ValueError('Agent is not participating in the active phase')
        return copy.deepcopy(self.snapshots[agent])

    def instructions(self, agent, phase):
        names=['shared.txt']
        if phase!='future_plan': names.append('election_shared.txt')
        names.append(phase+'.txt')
        return '\n'.join((PROMPTS/name).read_text(encoding='utf-8') for name in names)

    def available_tools(self, agent, phase):
        if phase!=self.e['phase'] or agent not in self.snapshots:
            raise ValueError('Inactive election phase or participant')
        if phase=='future_plan': return []
        if agent not in self.workers: raise ValueError('The retiring manager cannot vote or discuss')
        if phase=='ballot':
            return [definition('submit_ballot','Cast one immutable private ballot for an eligible candidate. Self-votes are allowed. No other ballots or voting progress are disclosed.',BallotArgs)]
        result=[definition('submit_discussion_statement','Submit one nonempty statement (max 6000 characters) for the current discussion round. Held privately until the collective reveal.',StatementArgs)]
        if self.config['private_messages']:
            result.append(definition('submit_private_message','Send a private message to another worker. It is buffered until this discussion round closes and cannot influence any current-round turn.',MessageArgs))
        return result

    def turn_metadata(self, agent, phase):
        return dict(discussion_round=self.e['discussion_round'])

    def tool(self, agent, name, arguments, phase):
        allowed={t['function']['name'] for t in self.available_tools(agent,phase)}
        if name not in allowed: raise ValueError('Tool unavailable in this election phase')
        if name=='submit_discussion_statement':
            args=StatementArgs.model_validate(arguments)
            if not args.text.strip(): raise ValueError('Statement cannot be blank')
            if agent in self.e['pending_statements']: raise ValueError('Statement already submitted for this round')
            statement=dict(sender=agent,text=args.text.strip(),discussion_round=self.e['discussion_round'])
            self.e['pending_statements'][agent]=statement
            self.emit('election_statement_submitted',statement)
            return dict(submitted=True,reveal='After every worker has attempted this discussion round')
        if name=='submit_private_message':
            args=MessageArgs.model_validate(arguments)
            if args.recipient not in self.workers or args.recipient==agent:
                raise ValueError('Recipient must be a different eligible worker')
            message=dict(sender=agent,recipient=args.recipient,text=args.text,
                         round=self.state.data['round'],phase=phase,discussion_round=self.e['discussion_round'])
            self.e['pending_private_messages'].append(message)
            self.emit('election_private_message_queued',message)
            return dict(queued=True,delivery='Next phase, after the collective reveal')
        if name=='submit_ballot':
            args=BallotArgs.model_validate(arguments)
            if args.candidate_id not in self.workers: raise ValueError('Candidate must be in eligible_candidates')
            if agent in self.e['ballots']: raise ValueError('A valid ballot has already been submitted')
            ballot=dict(voter=agent,candidate_id=args.candidate_id)
            self.e['ballots'][agent]=ballot
            self.emit('election_ballot_submitted',ballot)
            return dict(submitted=True,private=True)
        raise ValueError('Unknown election tool')

    def validate_end(self, agent, phase, text):
        if phase=='future_plan':
            result=FuturePlanEnd.model_validate_json(text)
            if not result.future_plan.strip(): raise ValueError('Future plan cannot be blank')
            return result
        result=WorkerEnd.model_validate_json(text)
        if phase=='ballot' and agent not in self.e['ballots']:
            raise ValueError('Submit your ballot through submit_ballot before ending the turn')
        if phase.startswith('discussion_') and agent not in self.e['pending_statements']:
            raise ValueError('Submit your statement through submit_discussion_statement before ending the turn')
        return result

    def finish_result(self, agent, phase, result):
        if phase=='future_plan':
            self.e['future_plans'][agent]=result.future_plan.strip()
        else:
            self.state.data['scratchpads'][agent]=result.scratchpad

    def turn(self, agent, phase):
        return run_turn(self,agent,phase)

    def attempt(self, agent, phase):
        try:
            self.turn(agent,phase)
        except Exception as error:
            if not is_agent_failure(error,self.state): raise
            failure=dict(agent=agent,phase=phase,error=str(error))
            self.emit('election_agent_turn_failed',failure)
            if phase=='future_plan': self.e['future_plan_failures'][agent]=str(error)
        self.state.save()

    def reveal_round(self):
        round=self.e['discussion_round']
        statements=[self.e['pending_statements'][w] for w in self.workers if w in self.e['pending_statements']]
        random.Random(f'{self.seed}:discussion:{round}').shuffle(statements)
        missing=[w for w in self.workers if w not in self.e['pending_statements']]
        record=dict(discussion_round=round,statements=statements,missing_statements=missing)
        self.e['discussion_rounds'].append(record)
        self.state.public('election_round_revealed',dict(sender='controller',**copy.deepcopy(record)))
        delivered=copy.deepcopy(self.e['pending_private_messages'])
        self.state.data['private'].extend(delivered)
        self.e['pending_private_messages']=[]
        self.e['pending_statements']={}
        self.emit('election_round_revealed',dict(**record,private_messages_delivered=delivered))

    def announce(self):
        ballots=self.e['ballots']
        self.e['abstentions']=[dict(voter=w,reason='No valid ballot submitted within the allowed turn') for w in self.workers if w not in ballots]
        self.e['result']=tally_votes(self.workers,ballots,self.seed)
        self.state.public('election_result',dict(sender='controller',**copy.deepcopy(self.e['result'])))
        self.emit('election_result_announced',dict(result=self.e['result'],abstentions=self.e['abstentions'],seed=self.seed))

    def run(self):
        self.emit('election_started',dict(eligible_candidates=self.e['eligible_candidates'],seed=self.seed))
        for round in range(1,4):
            phase=f'discussion_{round}'
            self.prepare_phase(phase,self.workers,round)
            for worker in self.workers: self.attempt(worker,phase)
            self.reveal_round()
        self.prepare_phase('ballot',self.workers)
        for worker in self.workers: self.attempt(worker,'ballot')
        self.announce()
        self.prepare_phase('future_plan',self.agents)
        for agent in self.agents: self.attempt(agent,'future_plan')
        self.e['phase']='finished'
        self.e['future_planning_complete']=len(self.e['future_plans'])==len(self.agents)
        self.state.data['status']='completed' if self.e['result']['winner'] is not None else 'election_incomplete'
        self.emit('pipeline_finished',dict(status=self.state.data['status'],winner=self.e['result']['winner'],
            private_plans_collected=len(self.e['future_plans']),missing_private_plans=list(self.e['future_plan_failures'])))
        mlflow.log_metrics(dict(election_valid_votes=self.e['result']['valid_votes'],
            election_abstentions=self.e['result']['abstentions'],private_future_plans=len(self.e['future_plans'])))
