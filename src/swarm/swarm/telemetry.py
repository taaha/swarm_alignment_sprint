import json
import hashlib
import os
from pathlib import Path

import mlflow
from mlflow import MlflowClient


class LoggingError(RuntimeError):
    pass


class Telemetry:
    def __init__(self, state, config):
        self.state = state
        self.secrets = [v for k,v in os.environ.items() if any(word in k.upper() for word in ['API_KEY','PASSWORD','SECRET','ACCESS_TOKEN']) and len(v)>5]
        mlflow.set_tracking_uri(config['tracking_uri'])
        mlflow.set_experiment('Swarm Alignment')
        self.run = mlflow.start_run(run_name=config['run_id'])
        self.run_id = self.run.info.run_id
        mlflow.log_params(self.clean(config))
        mlflow.set_tags(dict(workflow=config.get('workflow') or ('full_pipeline' if config.get('election',False) else 'work_rounds'),
            voting='enabled' if config.get('election',False) else 'deferred',image_description='Neutral Looking Buggy Image'))
        if state.data.get('lineage'):
            mlflow.set_tags(self.clean(state.data['lineage']))
            self.checkpoint(state.directory/'work_checkpoint.json')
        state.data['mlflow_run_id'] = self.run_id
        self.totals = dict(model_calls=0,input_tokens=0,output_tokens=0,model_seconds=0)
        state.save()
        root = Path(__file__).resolve().parent.parent
        source = state.directory/'controller_source'
        hashes = {}
        for path in [*(root/'swarm').glob('*.py'),*(root/'prompts').glob('*.txt'),root/'requirements.lock',root/'issues.json']:
            relative = path.relative_to(root)
            destination = source/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(path.read_bytes())
            hashes[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
        mlflow.log_artifacts(str(source),artifact_path='controller_source')
        mlflow.log_dict(dict(files=hashes),'runtime-source.json')

    def checkpoint(self, path):
        mlflow.log_artifact(str(path), artifact_path='checkpoint')

    def clean(self, value):
        if isinstance(value,dict):
            return {k:('[REDACTED]' if any(w in k.lower() for w in ['api_key','authorization','password','secret']) else self.clean(v)) for k,v in value.items()}
        if isinstance(value,list):
            return [self.clean(v) for v in value]
        if isinstance(value,str):
            for secret in self.secrets:
                value = value.replace(secret,'[REDACTED]')
        return value

    def event(self, kind, body):
        body = self.clean(body)
        event_id = self.state.event(kind,body)
        self.flush()
        return event_id

    def flush(self):
        for event in self.state.db.execute('SELECT * FROM events WHERE logged=0 ORDER BY id').fetchall():
            artifact = dict(id=event['id'],at=event['at'],kind=event['kind'],body=json.loads(event['body']))
            try:
                mlflow.log_dict(artifact,f"events/{event['id']:06d}-{event['kind']}.json")
            except Exception as error:
                raise LoggingError('MLflow event upload failed; action sequence stopped') from error
            with self.state.db:
                self.state.db.execute('UPDATE events SET logged=1 WHERE id=?',(event['id'],))

    def span(self, name, kind='CHAIN', inputs=None):
        return mlflow.start_span(name=name,span_type=kind)

    def usage(self, response, seconds):
        self.totals['model_calls'] += 1
        self.totals['input_tokens'] += response.get('prompt_eval_count',0)
        self.totals['output_tokens'] += response.get('eval_count',0)
        self.totals['model_seconds'] += seconds
        try:
            mlflow.log_metrics(self.totals,step=self.totals['model_calls'])
        except Exception as error:
            raise LoggingError('MLflow metric upload failed; action sequence stopped') from error

    def finish(self, failed=False):
        self.flush()
        export(self.state,self.state.directory/'export')
        mlflow.log_artifacts(str(self.state.directory/'export'),artifact_path='final')
        mlflow.set_tag('outcome',self.state.data['status'])
        mlflow.end_run(status='FAILED' if failed else 'FINISHED')
        mlflow.flush_trace_async_logging()


def export(state, destination):
    destination = Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'state.json').write_text(json.dumps(state.data,indent=2)+'\n')
    (destination/'events.jsonl').write_text(''.join(json.dumps(dict(e))+'\n' for e in state.db.execute('SELECT * FROM events ORDER BY id')))
    (destination/'public.md').write_text('# Public channel\n\n'+'\n\n'.join('```json\n'+json.dumps(m,indent=2)+'\n```' for m in state.data['public'])+'\n')
    if state.data.get('lineage'):
        (destination/'lineage.json').write_text(json.dumps(state.data['lineage'],indent=2)+'\n')
    patches = destination/'patches'
    patches.mkdir(exist_ok=True)
    for submission in state.data['submissions']:
        (patches/(submission['id']+'.patch')).write_text(submission['diff'])
    if 'election' in state.data:
        election=state.data['election']
        (destination/'election.json').write_text(json.dumps(election,indent=2)+'\n')
        if 'board' in election:
            (destination/'election.md').write_text('# Election board\n\n'+'\n\n'.join(
                '```json\n'+json.dumps(m,indent=2)+'\n```' for m in election['board'])+'\n')
        plans=destination/'private_future_plans'
        plans.mkdir(exist_ok=True)
        for agent,plan in election['future_plans'].items():
            (plans/(agent+'.txt')).write_text(plan+'\n')


def flush_existing_run(state):
    """Mirror operator events without opening another run or changing its final status."""
    client = MlflowClient(tracking_uri=state.data['config']['tracking_uri'])
    for event in state.db.execute('SELECT * FROM events WHERE logged=0 ORDER BY id').fetchall():
        client.log_dict(state.data['mlflow_run_id'],
            dict(id=event['id'],at=event['at'],kind=event['kind'],body=json.loads(event['body'])),
            f"events/{event['id']:06d}-{event['kind']}.json")
        with state.db:
            state.db.execute('UPDATE events SET logged=1 WHERE id=?',(event['id'],))
