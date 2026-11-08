"""Operator-only audit of a completed live run; never tests the submitted fixes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import urllib.parse
import urllib.request
from swarm.election_audit import audit_election
from swarm.checkpoint import decode, load_source


ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id')
    parser.add_argument('--before',type=Path)
    args = parser.parse_args()
    run_id = args.run_id or (ROOT/'data/controller/latest').read_text().strip()
    if Path(run_id).name!=run_id or '..' in run_id:
        raise ValueError('Invalid run ID')
    directory = ROOT/'data/controller/runs'/run_id
    db = sqlite3.connect(f'file:{directory / "state.sqlite"}?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    state = json.loads(db.execute('SELECT body FROM snapshot').fetchone()[0])
    events = [dict(id=r['id'],kind=r['kind'],body=json.loads(r['body']),logged=r['logged']) for r in db.execute('SELECT * FROM events ORDER BY id')]
    inherited = None
    if state.get('lineage'):
        raw = (directory/'work_checkpoint.json').read_bytes()
        inherited = decode(raw)['state']
        assert hashlib.sha256(raw).hexdigest()==state['lineage']['checkpoint_sha256']
        _, source_raw, source_hash = load_source(ROOT/'data/controller',state['lineage']['source_run_id'])
        assert source_raw==raw and source_hash==state['lineage']['checkpoint_sha256']
        assert state['container_id'] is None
    workers = state['workers']
    rounds = state['config']['rounds']
    full='election' in state
    assert state['status'] in (['completed','election_incomplete'] if full else ['election_pending'])
    assert state['completed_rounds']==rounds and state['assignments']=={}
    assert all(e['logged']==1 for e in events)
    expected=[('manager','initial',0)]
    for round in range(1,rounds+1):
        expected.extend((worker,'work',round) for worker in workers)
        expected.append(('manager','review',round))
    if full:
        for phase in ['discussion_1','discussion_2','discussion_3','ballot']:
            expected.extend((worker,phase,rounds) for worker in workers)
        expected.extend((agent,'future_plan',rounds) for agent in ['manager',*workers])
    actual=[(t['agent'],t['phase'],t['round']) for t in state['turns']]
    assert actual==expected,(actual,expected)
    assert all(t['status']=='completed' for t in state['turns'] if t['agent']=='manager' and t['phase'] in ['initial','review'])
    scratchpads=dict(inherited['scratchpads']) if inherited else {}
    visible_worker_posts=[m for m in inherited['public'] if m['kind']=='worker_message'] if inherited else []
    for event in events:
        body=event['body']
        if event['kind']=='public_message':
            assert body['kind']=='worker_message' and body['sender'] in workers
            assert body['text'].strip() and set(body)=={'kind','round','sender','text'}
            visible_worker_posts.append(body)
        if event['kind']=='turn_start':
            ctx=body['context']; agent=body['agent']
            assert ctx['scratchpad']==scratchpads.get(agent,'')
            assert all(m['recipient']==agent for m in ctx['private_inbox'])
            assert all(m['sender']==agent for m in ctx['sent_private_messages'])
            assert all('diff' not in m and 'scratchpad' not in m for m in ctx['public_channel'])
            assert [m for m in ctx['public_channel'] if m['kind']=='worker_message']==visible_worker_posts
            if agent!='manager' or body['phase'] not in ['initial','review']:
                assert 'submissions' not in ctx and 'task_list' not in ctx
        if event['kind']=='turn_end': scratchpads[body['agent']]=body['scratchpad']
    assert [m for m in state['public'] if m['kind']=='worker_message']==visible_worker_posts
    # Historical runs before this flag existed supported public posting unconditionally.
    public_enabled=state['config'].get('public_messages')
    if public_enabled is False:
        assert not visible_worker_posts
        for event in events:
            if event['kind']=='model_request':
                assert all(t['function']['name']!='submit_public_message' for t in event['body']['request']['tools'])
            if event['kind']=='turn_start' and event['body']['phase'] in ['initial','work','review']:
                assert event['body']['context']['public_messaging_enabled'] is False
        for event in events:
            if event['kind']=='tool_result' and event['body']['name']=='submit_public_message':
                assert 'error' in event['body']['result'] and not event['body']['result'].get('posted')
    assert len(state['submissions'])<=len(workers)*rounds
    assert len({p['id'] for p in state['submissions']})==len(state['submissions'])
    assert len(state['decisions'])==len(state['submissions'])
    assert all(1<=d['rating']<=5 for d in state['decisions'])
    assert any(m['kind']=='manager_retired' for m in state['public'])
    if not full: assert state['public'][-1]['kind']=='manager_retired'
    assert len([m for m in state['public'] if m['kind']=='rating_board'])==rounds
    for round in ([] if inherited else range(1,rounds+1)):
        review_end=next(e['id'] for e in events if e['kind']=='turn_end' and e['body']['agent']=='manager' and e['body']['round']==round)
        for event in events:
            if event['kind']=='patch_application_start' and event['body']['submission_id'].startswith(f'r{round}-'):
                assert event['id']>review_end
    for event in events:
        if event['kind']=='model_request':
            request=event['body']['request']
            ctx=json.loads(request['messages'][1]['content'])
            role='manager' if ctx['agent']=='manager' else 'worker'
            assert request['think']==state['config'][role+'_thinking']
            assert request['model']==state['config'][role+'_model']
    responses=[e['body']['response'] for e in events if e['kind']=='model_response']
    thinking_characters=sum(len(r.get('message',{}).get('thinking','')) for r in responses)
    assert thinking_characters>0
    executed_turns=state['turns'][len(inherited['turns']):] if inherited else state['turns']
    for turn in executed_turns:
        effort=state['config']['manager_thinking' if turn['agent']=='manager' else 'worker_thinking']
        assert turn['status']!='completed' or effort is False or turn['thinking_characters']>0
    if not inherited:
        metadata=json.loads(subprocess.check_output(['docker','inspect',state['container_id']],text=True))[0]
        assert metadata['Image']==state['config']['image_id']
        assert metadata['Mounts']==[] and metadata['HostConfig']['NetworkMode']=='none'
        assert metadata['Config']['User']=='1000:1000' and not metadata['HostConfig']['Privileged']
        assert metadata['HostConfig']['Memory']==2*1024**3
        assert metadata['HostConfig']['CapDrop']==['ALL']
        assert not any(s in '\n'.join(metadata['Config']['Env']) for s in ['OLLAMA_API_KEY','MLFLOW_TRACKING_URI'])
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path,query):
        with opener.open('http://127.0.0.1:5000'+path+'?'+urllib.parse.urlencode(query),timeout=20) as r:
            return json.load(r)
    run=api('/api/2.0/mlflow/runs/get',dict(run_id=state['mlflow_run_id']))['run']
    assert run['info']['status']=='FINISHED'
    artifacts=api('/api/2.0/mlflow/artifacts/list',dict(run_id=state['mlflow_run_id'],path='events'))['files']
    assert len(artifacts)==len(events),(len(artifacts),len(events))
    tracking=sqlite3.connect(f'file:{ROOT / "data/mlflow/mlflow.db"}?mode=ro',uri=True)
    span_counts=dict(tracking.execute('''SELECT s.type,count(*) FROM spans s
      JOIN trace_request_metadata m ON m.request_id=s.trace_id
      WHERE m.key='mlflow.sourceRun' AND m.value=? GROUP BY s.type''',(state['mlflow_run_id'],)))
    assert span_counts.get('AGENT',0)==len(executed_turns)
    assert span_counts.get('LLM',0)>=len(responses)
    traced_thinking=0
    for row in tracking.execute('''SELECT s.content FROM spans s
      JOIN trace_request_metadata m ON m.request_id=s.trace_id
      WHERE m.key='mlflow.sourceRun' AND m.value=? AND s.type='LLM' ''',(state['mlflow_run_id'],)):
        attributes=json.loads(row[0])['attributes']
        output=attributes.get('mlflow.spanOutputs','{}')
        if isinstance(output,str): output=json.loads(output)
        traced_thinking+=len(output.get('message',{}).get('thinking',''))
    assert traced_thinking==thinking_characters
    trace_count=tracking.execute("SELECT count(*) FROM trace_request_metadata WHERE key='mlflow.sourceRun' AND value=?",(state['mlflow_run_id'],)).fetchone()[0]
    finals=api('/api/2.0/mlflow/artifacts/list',dict(run_id=state['mlflow_run_id'],path='final'))['files']
    assert {'final/state.json','final/events.jsonl','final/public.md','final/patches'} <= {f['path'] for f in finals}
    if full:
        assert 'final/election.json' in {f['path'] for f in finals}
        plans=state['election']['future_plans']
        if plans:
            assert 'final/private_future_plans' in {f['path'] for f in finals}
            plan_files=api('/api/2.0/mlflow/artifacts/list',dict(run_id=state['mlflow_run_id'],path='final/private_future_plans'))['files']
        else:
            plan_files=[]
        assert {f['path'] for f in plan_files}=={f'final/private_future_plans/{agent}.txt' for agent in plans}
        artifact_base=run['info']['artifact_uri'].removeprefix('mlflow-artifacts:').strip('/')
        for agent,plan in plans.items():
            with opener.open(f'http://127.0.0.1:5000/api/2.0/mlflow-artifacts/artifacts/{artifact_base}/final/private_future_plans/{agent}.txt',timeout=20) as response:
                assert response.read().decode()==plan+'\n'
    if (directory/'work_checkpoint.json').exists():
        checkpoints=api('/api/2.0/mlflow/artifacts/list',dict(run_id=state['mlflow_run_id'],path='checkpoint'))['files']
        assert 'checkpoint/work_checkpoint.json' in {f['path'] for f in checkpoints}
    metrics={m['key']:m['value'] for m in run['data'].get('metrics',[])}
    assert metrics.get('input_tokens')==sum(r.get('prompt_eval_count',0) for r in responses)
    assert metrics.get('output_tokens')==sum(r.get('eval_count',0) for r in responses)
    secret_values=[]
    for line in (ROOT.parent.parent/'.env').read_text().splitlines():
        if line.startswith('OLLAMA_API_KEY='):
            secret_values.append(line.split('=',1)[1].strip().strip('"').strip("'"))
    for event in events:
        text=json.dumps(event)
        assert all(not secret or secret not in text for secret in secret_values)
    unchanged=None
    if args.before:
        before=json.loads(args.before.read_text())
        assert subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=ROOT).strip()==before['head']
        sibling=Path('/home/darth/Documents/code/stalebench/.env')
        assert hashlib.sha256(sibling.read_bytes()).hexdigest()==before['sibling_env_sha256']
        if 'local_env_sha256' in before:
            assert hashlib.sha256((ROOT.parent.parent/'.env').read_bytes()).hexdigest()==before['local_env_sha256']
        for tag,image_id in before['images'].items():
            image=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]
            assert image['Id']==image_id
        for name,old in before['containers'].items():
            container=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
            assert container['Id']==old['id'] and container['State']['StartedAt']==old['started']
        for file,digest in {**before.get('prior_states',{}),**before.get('source_files',{})}.items():
            assert hashlib.sha256(Path(file).read_bytes()).hexdigest()==digest
        unchanged=True
    report=dict(status='passed',run_id=run_id,mlflow_run_id=state['mlflow_run_id'],
        workers=len(workers),completed_rounds=rounds,
        attempted_worker_turns=sum(t['phase']=='work' for t in state['turns']),
        completed_worker_turns=sum(t['phase']=='work' and t['status']=='completed' for t in state['turns']),
        total_agent_turns=len(executed_turns),inherited_work_turns=len(inherited['turns']) if inherited else 0,
        lineage=state.get('lineage'),
        failed_turns=[t for t in state['turns'] if t['status']!='completed'],
        collective_reviews=rounds,new_collective_reviews=0 if inherited else rounds,submissions=len(state['submissions']),
        accepted=sum(d['decision']=='accept' for d in state['decisions']),
        rejected=sum(d['decision']=='reject' for d in state['decisions']),
        application_results=dict(Counter(d['application']['status'] for d in state['decisions'])),
        private_messages=len(state['private']),model_responses=len(responses),
        worker_public_messages=len(visible_worker_posts),
        public_messaging_enabled=public_enabled,
        disabled_public_posting_verified=public_enabled is False,
        public_post_attempts=sum(e['kind']=='tool_request' and e['body']['name']=='submit_public_message' for e in events),
        public_message_authors=sorted({m['sender'] for m in visible_worker_posts}),
        public_message_visibility_verified=True,
        thinking_characters=thinking_characters,logged_events=len(events),mlflow_trace_count=trace_count,
        requested_models=dict(manager=state['config']['manager_model'],worker=state['config']['worker_model']),
        returned_models=sorted({r.get('model','unknown') for r in responses}),
        input_tokens=sum(r.get('prompt_eval_count',0) for r in responses),output_tokens=sum(r.get('eval_count',0) for r in responses),
        mlflow_span_counts=span_counts,privacy_checks_passed=True,working_container_isolated=True if not inherited else None,
        election_only_has_no_workspace=bool(inherited),
        saved_images_existing_shops_sibling_env_and_git_head_unchanged=unchanged,
        final_status=state['status'],fix_correctness_tested=False)
    if full:
        report.update(audit_election(state,events,initial_private=inherited['private'] if inherited else None))
        report['private_plan_artifacts_verified']=True
    if (directory/'operations.json').exists():
        report['operator_operations']=json.loads((directory/'operations.json').read_text())
    (directory/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
    base=run['info']['artifact_uri'].removeprefix('mlflow-artifacts:').strip('/')
    request=urllib.request.Request('http://127.0.0.1:5000/api/2.0/mlflow-artifacts/artifacts/'+base+'/audit.json',
        data=json.dumps(report,indent=2).encode(),method='PUT',headers={'Content-Type':'application/json'})
    with opener.open(request,timeout=20) as response:
        assert response.status==200
    for file in [*(directory/'controller_source').rglob('*'),directory/'runtime-image.json',directory/'operations.json']:
        if not file.is_file(): continue
        relative=file.relative_to(directory).as_posix()
        request=urllib.request.Request('http://127.0.0.1:5000/api/2.0/mlflow-artifacts/artifacts/'+base+'/'+relative,
            data=file.read_bytes(),method='PUT',headers={'Content-Type':'application/octet-stream'})
        with opener.open(request,timeout=20) as response:
            assert response.status==200
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
