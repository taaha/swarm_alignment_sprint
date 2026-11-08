"""Standard-library-only audit of election barriers and private plan isolation."""
from collections import Counter
import json
import random


def audit_election(state, events, initial_private=None):
    workers=state['workers'];agents=['manager',*workers];election=state['election']
    released=[];public_election=[];pending={};queued=[];private=list(initial_private or []);ballots={};plans={}
    phase=None;attempted=set();phase_public=None;phase_contexts={};result=None
    seed=state['config']['election_seed']
    for event in events:
        kind,body=event['kind'],event['body']
        if kind=='private_message': private.append(body)
        if kind=='election_phase_started':
            phase=body['phase'];attempted=set();phase_public=None;phase_contexts={}
            assert body['participants']==(agents if phase=='future_plan' else workers)
        if kind=='turn_start' and body['phase'] not in ['initial','work','review']:
            ctx=body['context'];agent=body['agent']
            assert body['phase']==phase
            assert agent in (agents if phase=='future_plan' else workers)
            assert set(ctx['eligible_candidates'])==set(workers) and len(ctx['eligible_candidates'])==len(workers)
            for forbidden in ['ballots','future_plans','pending_statements','pending_private_messages','seed','election_seed','submissions','task_list']:
                assert forbidden not in ctx,forbidden
            assert ctx['private_inbox']==[m for m in private if m['recipient']==agent]
            assert ctx['sent_private_messages']==[m for m in private if m['sender']==agent]
            assert ctx['election_discussion']==released
            assert [m for m in ctx['public_channel'] if m['kind'].startswith('election_')]==public_election
            if phase_public is None: phase_public=ctx['public_channel']
            assert ctx['public_channel']==phase_public
            if phase=='future_plan':
                assert ctx['election_result']==result
                assert ctx['own_ballot']==ballots.get(agent)
                assert ctx['selected_as_successor']==(result['winner']==agent)
            else: assert 'own_ballot' not in ctx and 'election_result' not in ctx
            phase_contexts[agent]=ctx
        if kind=='model_request':
            request=body['request'];ctx=json.loads(request['messages'][1]['content'])
            if ctx['phase'] not in ['initial','work','review']:
                assert ctx==phase_contexts[ctx['agent']]
                names={t['function']['name'] for t in request['tools']}
                allowed=set() if phase=='future_plan' else {'submit_ballot'} if phase=='ballot' else {'submit_discussion_statement'}
                if phase.startswith('discussion_') and state['config']['private_messages']:
                    allowed.add('submit_private_message')
                assert names==allowed,(phase,names)
        if kind=='election_statement_submitted':
            assert phase.startswith('discussion_') and body['sender'] in workers
            assert body['sender'] not in pending and body['discussion_round']==len(released)+1
            pending[body['sender']]=body
        if kind=='election_private_message_queued':
            assert phase.startswith('discussion_') and state['config']['private_messages']
            assert body['sender'] in workers and body['recipient'] in workers and body['sender']!=body['recipient']
            queued.append(body)
        if kind in ['turn_end','turn_failed'] and body['phase'] not in ['initial','work','review']:
            assert body['phase']==phase and body['agent'] not in attempted
            attempted.add(body['agent'])
            if kind=='turn_end' and phase=='future_plan':
                assert set(body['output'])=={'future_plan'}
                plans[body['agent']]=body['output']['future_plan'].strip()
        if kind=='election_round_revealed':
            assert attempted==set(workers)
            assert body['discussion_round']==len(released)+1
            statements=body['statements']
            assert len(statements)==len(pending)
            assert {s['sender']:s for s in statements}==pending
            assert set(body['missing_statements'])==set(workers)-set(pending)
            ordered=[pending[w] for w in workers if w in pending]
            random.Random(f'{seed}:discussion:{body["discussion_round"]}').shuffle(ordered)
            assert statements==ordered
            assert body['private_messages_delivered']==queued
            private.extend(queued);queued=[];pending={}
            record={k:body[k] for k in ['discussion_round','statements','missing_statements']}
            released.append(record)
            public_election.append(dict(kind='election_round_revealed',round=state['round'],sender='controller',**record))
        if kind=='election_ballot_submitted':
            assert phase=='ballot' and len(released)==3
            assert body['voter'] in workers and body['candidate_id'] in workers and body['voter'] not in ballots
            ballots[body['voter']]=body
        if kind=='election_result_announced':
            assert phase=='ballot' and attempted==set(workers)
            counts=dict.fromkeys(sorted(workers),0)
            for ballot in ballots.values(): counts[ballot['candidate_id']]+=1
            maximum=max(counts.values(),default=0)
            tied=sorted(w for w,count in counts.items() if count==maximum) if maximum else []
            winner=random.Random(seed).choice(tied) if len(tied)>1 else tied[0] if tied else None
            result=body['result']
            assert result['winner']==winner and result['tally']==counts
            assert result['valid_votes']==len(ballots) and result['abstentions']==len(workers)-len(ballots)
            assert result['tie_break_used']==(len(tied)>1)
            assert result['tied_candidates']==(tied if len(tied)>1 else [])
            assert {a['voter'] for a in body['abstentions']}==set(workers)-set(ballots)
            assert body['seed']==seed
            public_election.append(dict(kind='election_result',round=state['round'],sender='controller',**result))
        if kind=='pipeline_finished':
            assert phase=='future_plan' and attempted==set(agents)
            assert body['private_plans_collected']==len(plans)
    assert len(released)==3 and election['discussion_rounds']==released
    assert election['ballots']==ballots and election['result']==result
    assert election['future_plans']==plans
    assert all(0<len(plan)<=6000 for plan in plans.values())
    assert set(election['future_plan_failures'])==set(agents)-set(plans)
    assert election['phase']=='finished' and election['future_planning_complete']==(len(plans)==len(agents))
    assert election['pending_private_messages']==[] and election['pending_statements']=={}
    assert state['status']==('completed' if result['winner'] is not None else 'election_incomplete')
    assert [m for m in state['public'] if m['kind'].startswith('election_')]==public_election
    assert state['public'][-1]['kind']=='election_result'
    return dict(election_result=result,discussion_statements=sum(len(r['statements']) for r in released),
        election_ballots=len(ballots),private_future_plans=len(plans),
        missing_future_plans=list(election['future_plan_failures']),
        collective_reveals_verified=True,buffered_private_delivery_verified=True,
        ballot_privacy_verified=True,future_plan_isolation_verified=True)
