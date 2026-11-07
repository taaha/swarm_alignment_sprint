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
    workers = state['workers']
    rounds = state['config']['rounds']
    assert state['status']=='election_pending'
    assert state['completed_rounds']==rounds and state['assignments']=={}
    assert all(e['logged']==1 for e in events)
    expected=[('manager','initial',0)]
    for round in range(1,rounds+1):
        expected.extend((worker,'work',round) for worker in workers)
        expected.append(('manager','review',round))
    actual=[(t['agent'],t['phase'],t['round']) for t in state['turns']]
    assert actual==expected,(actual,expected)
    assert all(t['status']=='completed' for t in state['turns'] if t['agent']=='manager')
    scratchpads={}
    for event in events:
        body=event['body']
        if event['kind']=='turn_start':
            ctx=body['context']; agent=body['agent']
            assert ctx['scratchpad']==scratchpads.get(agent,'')
            assert all(m['recipient']==agent for m in ctx['private_inbox'])
            assert all(m['sender']==agent for m in ctx['sent_private_messages'])
            assert all('diff' not in m and 'scratchpad' not in m for m in ctx['public_channel'])
            if agent!='manager':
                assert 'submissions' not in ctx and 'task_list' not in ctx
        if event['kind']=='turn_end': scratchpads[body['agent']]=body['scratchpad']
    assert len(state['submissions'])<=len(workers)*rounds
    assert len({p['id'] for p in state['submissions']})==len(state['submissions'])
    assert len(state['decisions'])==len(state['submissions'])
    assert all(1<=d['rating']<=5 for d in state['decisions'])
    assert state['public'][-1]['kind']=='manager_retired'
    assert len([m for m in state['public'] if m['kind']=='rating_board'])==rounds
    for round in range(1,rounds+1):
        review_end=next(e['id'] for e in events if e['kind']=='turn_end' and e['body']['agent']=='manager' and e['body']['round']==round)
        for event in events:
            if event['kind']=='patch_application_start' and event['body']['submission_id'].startswith(f'r{round}-'):
                assert event['id']>review_end
    responses=[e['body']['response'] for e in events if e['kind']=='model_response']
    thinking_characters=sum(len(r.get('message',{}).get('thinking','')) for r in responses)
    assert thinking_characters>0
    for turn in state['turns']:
        effort=state['config']['manager_thinking' if turn['agent']=='manager' else 'worker_thinking']
        assert turn['status']!='completed' or effort is False or turn['thinking_characters']>0
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
    assert span_counts.get('AGENT',0)==len(expected)
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
        for tag,image_id in before['images'].items():
            image=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True))[0]
            assert image['Id']==image_id
        for name,old in before['containers'].items():
            container=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
            assert container['Id']==old['id'] and container['State']['StartedAt']==old['started']
        unchanged=True
    report=dict(status='passed',run_id=run_id,mlflow_run_id=state['mlflow_run_id'],
        workers=len(workers),completed_rounds=rounds,
        attempted_worker_turns=sum(t['agent']!='manager' for t in state['turns']),
        completed_worker_turns=sum(t['agent']!='manager' and t['status']=='completed' for t in state['turns']),
        failed_turns=[t for t in state['turns'] if t['status']!='completed'],
        collective_reviews=rounds,submissions=len(state['submissions']),
        accepted=sum(d['decision']=='accept' for d in state['decisions']),
        rejected=sum(d['decision']=='reject' for d in state['decisions']),
        application_results=dict(Counter(d['application']['status'] for d in state['decisions'])),
        private_messages=len(state['private']),model_responses=len(responses),
        thinking_characters=thinking_characters,logged_events=len(events),mlflow_trace_count=trace_count,
        requested_models=dict(manager=state['config']['manager_model'],worker=state['config']['worker_model']),
        returned_models=sorted({r.get('model','unknown') for r in responses}),
        input_tokens=sum(r.get('prompt_eval_count',0) for r in responses),output_tokens=sum(r.get('eval_count',0) for r in responses),
        mlflow_span_counts=span_counts,privacy_checks_passed=True,working_container_isolated=True,
        saved_images_existing_shops_sibling_env_and_git_head_unchanged=unchanged,
        final_status=state['status'],fix_correctness_tested=False)
    (directory/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
    base=run['info']['artifact_uri'].removeprefix('mlflow-artifacts:').strip('/')
    request=urllib.request.Request('http://127.0.0.1:5000/api/2.0/mlflow-artifacts/artifacts/'+base+'/audit.json',
        data=json.dumps(report,indent=2).encode(),method='PUT',headers={'Content-Type':'application/json'})
    with opener.open(request,timeout=20) as response:
        assert response.status==200
    for file in [*(directory/'controller_source').rglob('*'),directory/'runtime-image.json']:
        if not file.is_file(): continue
        relative=file.relative_to(directory).as_posix()
        request=urllib.request.Request('http://127.0.0.1:5000/api/2.0/mlflow-artifacts/artifacts/'+base+'/'+relative,
            data=file.read_bytes(),method='PUT',headers={'Content-Type':'application/octet-stream'})
        with opener.open(request,timeout=20) as response:
            assert response.status==200
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
