"""Read-only verification of recorded concurrency and stage barriers."""
import json


def audit_parallel(state, events):
    stage=None;active=set();attempted=set();ended=set();contexts={};requests={}
    stages=[];maximum_requests=0;queued=[];delivered_private=[]
    for event in events:
        kind,b=event['kind'],event['body']
        if kind=='parallel_stage_started':
            assert stage is None and not active and not requests
            stage=dict(**b,start_event=event['id'],max_observed_requests=0)
            assert 1<=b['concurrency']<=state['config'].get('concurrency',5)
            assert len(b['participants'])==len(set(b['participants']))
            attempted=set();ended=set();contexts={}
        if kind=='parallel_turn_started':
            assert stage and b['agent'] in stage['participants'] and b['agent'] not in attempted
            attempted.add(b['agent']);active.add(b['agent'])
            assert b['active_turns']==len(active)<=stage['concurrency']
            assert list(stage['participants'])[:len(attempted)]==[a for a in stage['participants'] if a in attempted]
        if kind=='turn_start':
            if stage:
                assert b['phase']==stage['phase'] and b['round']==stage['round']
                assert b['agent'] in active
                contexts[b['agent']]=b['context']
                if stage['phase']=='work':
                    assert b['context']['public_channel']==next(iter(contexts.values()))['public_channel']
                    assert not any(m['round']==b['round'] and m['sender'] in state['workers'] for m in b['context']['private_inbox'])
            else:
                assert b['agent']=='manager' and b['phase'] in ['initial','review']
        if kind in ['turn_end','turn_failed'] and stage:
            assert b['agent'] in active and b['phase']==stage['phase']
            assert b['agent'] not in ended
            ended.add(b['agent'])
        if kind=='parallel_turn_finished':
            assert b['agent'] in active
            active.remove(b['agent'])
            assert b['active_turns']==len(active)
        if kind=='model_request':
            ctx=json.loads(b['request']['messages'][1]['content'])
            key=tuple(ctx.get(k) for k in ['agent','phase','round','discussion_round'])
            assert key not in requests
            assert all(b.get(k)==ctx.get(k) for k in ['agent','phase','round','discussion_round'])
            requests[key]=event['id']
            maximum_requests=max(maximum_requests,len(requests))
            if stage:
                assert ctx==contexts[ctx['agent']]
                stage['max_observed_requests']=max(stage['max_observed_requests'],len(requests))
                assert len(requests)<=stage['concurrency']
        if kind in ['model_response','model_error']:
            key=tuple(b.get(k) for k in ['agent','phase','round','discussion_round'])
            assert key in requests
            requests.pop(key)
        if kind=='parallel_stage_finished':
            assert stage and not active and not requests
            assert attempted==ended==set(stage['participants'])
            assert b['participants']==stage['participants'] and b['phase']==stage['phase']
            stages.append(dict(phase=b['phase'],round=b['round'],discussion_round=b['discussion_round'],
                participants=len(b['participants']),max_active_turns=b['max_active_turns'],
                max_overlapping_model_requests=stage['max_observed_requests'],seconds=b['seconds']))
            stage=None
        if kind=='work_message_queued':
            assert stage and stage['phase']=='work'
            queued.append(b)
        if kind=='work_messages_delivered':
            assert stage is None
            expected=[m for w in state['workers'] for m in queued if m['message']['sender']==w]
            assert b['messages']==expected
            delivered_private.extend(m['message'] for m in expected if m['visibility']=='private')
            queued=[]
        if kind in ['patch_application_start','review_published','manager_retired','election_round_revealed','election_result_announced']:
            assert stage is None
    assert stage is None and not requests and not active and not queued
    assert state.get('pending_work_messages',[])==[]
    return dict(concurrency=state['config'].get('concurrency',5),
        maximum_overlapping_model_requests=maximum_requests,parallel_stages=stages,
        parallel_barriers_verified=True,work_message_barriers_verified=True)
