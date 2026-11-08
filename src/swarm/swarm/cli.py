import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
import uuid

import docker

from .checkpoint import load_source, save_checkpoint, election_state, valid_run_id, NAME
from .controller import Controller
from .election import Election
from .provider import Ollama
from .state import State
from .telemetry import Telemetry, export, flush_existing_run
from .workspace import Workspace, IMAGE_ID


def boolean(value):
    if isinstance(value,bool):
        return value
    if value.lower() in ['true','1','yes','on']:
        return True
    if value.lower() in ['false','0','no','off']:
        return False
    raise argparse.ArgumentTypeError('Expected true or false')


def thinking(value):
    if isinstance(value,bool):
        return value
    if value.lower() in ['on','true']:
        return True
    return False if value.lower() in ['off','false','none'] else value


def parser():
    p=argparse.ArgumentParser(description='Work rounds, succession election, and private future plans.')
    p.add_argument('command',choices=['run','resume-election','status','export','cleanup'])
    p.add_argument('--run-id')
    p.add_argument('--from-run')
    p.add_argument('--workers',type=int,default=int(os.getenv('SWARM_WORKERS','5')))
    p.add_argument('--concurrency',type=int,default=int(os.getenv('SWARM_CONCURRENCY','5')))
    p.add_argument('--rounds',type=int,default=int(os.getenv('SWARM_ROUNDS','3')))
    p.add_argument('--private-messages',type=boolean,default=boolean(os.getenv('SWARM_PRIVATE_MESSAGES','true')))
    p.add_argument('--public-messages',type=boolean,default=boolean(os.getenv('SWARM_PUBLIC_MESSAGES','false')))
    p.add_argument('--election',type=boolean,default=boolean(os.getenv('SWARM_ELECTION_ENABLED','true')))
    p.add_argument('--election-seed',type=int,default=int(os.getenv('SWARM_ELECTION_SEED','42')))
    p.add_argument('--manager-model',default=os.getenv('SWARM_MANAGER_MODEL',os.getenv('OLLAMA_MODEL','deepseek-v4.1-flash')))
    p.add_argument('--worker-model',default=os.getenv('SWARM_WORKER_MODEL',os.getenv('OLLAMA_MODEL','deepseek-v4.1-flash')))
    p.add_argument('--manager-thinking',default=os.getenv('SWARM_MANAGER_THINKING','high'))
    p.add_argument('--worker-thinking',default=os.getenv('SWARM_WORKER_THINKING','high'))
    p.add_argument('--max-tokens',type=int,default=int(os.getenv('SWARM_MAX_TOKENS','12000')))
    p.add_argument('--worker-call-limit',type=int,default=int(os.getenv('SWARM_WORKER_CALL_LIMIT','48')))
    p.add_argument('--manager-call-limit',type=int,default=int(os.getenv('SWARM_MANAGER_CALL_LIMIT','40')))
    p.add_argument('--temperature',type=float,default=float(os.getenv('OLLAMA_TEMPERATURE','0.7')))
    return p


def main():
    args=parser().parse_args()
    root=Path(os.getenv('SWARM_DATA','/state'))
    root.mkdir(parents=True,exist_ok=True)
    if args.command not in ['run','resume-election']:
        run_id=args.run_id or (root/'latest').read_text().strip()
        if not run_id or Path(run_id).name!=run_id or '..' in run_id:
            raise ValueError('Invalid run ID')
        state=State(root/'runs'/run_id)
        if args.command=='export':
            export(state,state.directory/'export')
            print(state.directory/'export')
        elif args.command=='cleanup':
            if state.data['status'] in ['created','running'] or (state.data['status']=='election_pending' and state.data.get('config',{}).get('election',False)):
                raise ValueError('Refusing to clean up an active run')
            if not state.data.get('container_id'):
                print('No working container was created; run records retained.')
                return
            client=docker.from_env()
            container=client.containers.get(state.data['container_id'])
            if container.labels.get('swarm-work.run')!=run_id:
                raise ValueError('Container ownership mismatch')
            state.event('workspace_cleanup_requested',dict(container_id=state.data['container_id']))
            flush_existing_run(state)
            container.remove(force=True)
            state.event('workspace_cleaned',dict(container_id=state.data['container_id']))
            flush_existing_run(state)
            print('Removed working container; run records retained.')
        else:
            print(json.dumps(dict(run_id=run_id,status=state.data['status'],completed_rounds=state.data['completed_rounds'],
                mlflow_run_id=state.data.get('mlflow_run_id'),container_id=state.data.get('container_id'),
                board=state.board(state.data.get('workers',[])),lineage=state.data.get('lineage')),indent=2))
            if 'election' in state.data:
                value=state.data['election']
                print(json.dumps(dict(election_phase=value['phase'],discussion_round=value['discussion_round'],
                    result=value['result'],private_plans_collected=len(value['future_plans']),
                    missing_private_plans=list(value['future_plan_failures'])),indent=2))
        return
    resumed = args.command == 'resume-election'
    source = raw = digest = None
    if resumed:
        if not args.from_run:
            raise ValueError('--from-run is required for resume-election')
        source, raw, digest = load_source(root, args.from_run)
        config = dict(source['config'])
        explicit = {word.split('=')[0] for word in sys.argv[1:] if word.startswith('--')}
        forbidden = {'--workers', '--rounds', '--public-messages', '--election'} & explicit
        if forbidden:
            raise ValueError('Cannot override inherited work settings: '+', '.join(sorted(forbidden)))
        for name in ['private_messages','election_seed','manager_model','worker_model','manager_thinking',
                     'worker_thinking','concurrency','max_tokens','worker_call_limit','manager_call_limit','temperature']:
            if '--'+name.replace('_','-') in explicit:
                config[name] = getattr(args,name)
        config.setdefault('concurrency',5)
        config['election'] = True
        config['workflow'] = 'election_only'
    else:
        if args.from_run:
            raise ValueError('--from-run is only valid with resume-election')
        config={k:v for k,v in vars(args).items() if k not in ['command','run_id','from_run']}
    if not 1<=config['workers']<=100 or config['rounds']<1 or min(config['max_tokens'],config['worker_call_limit'],config['manager_call_limit'],config['concurrency'])<1:
        raise ValueError('Worker count must be 1–100; rounds and limits must be positive')
    if not os.getenv('OLLAMA_API_KEY'):
        raise ValueError('OLLAMA_API_KEY is missing')
    run_id=valid_run_id(args.run_id or ('election-' if resumed else 'work-')+uuid.uuid4().hex[:12])
    directory=root/'runs'/run_id
    # Atomic reservation also prevents two controllers from using the same destination.
    directory.mkdir(parents=True,exist_ok=False)
    state=State(directory)
    if resumed:
        state.data = election_state(source, args.from_run, digest)
        (directory/NAME).write_bytes(raw)
    config.update(run_id=run_id,tracking_uri=os.getenv('MLFLOW_TRACKING_URI','http://mlflow:5000'),image_id=source['config']['image_id'] if resumed else IMAGE_ID)
    config['manager_thinking']=thinking(config['manager_thinking']) if isinstance(config['manager_thinking'],str) else config['manager_thinking']
    config['worker_thinking']=thinking(config['worker_thinking']) if isinstance(config['worker_thinking'],str) else config['worker_thinking']
    state.data['config']=config
    state.save()
    (root/'latest').write_text(run_id+'\n')
    telemetry=None
    try:
        telemetry=Telemetry(state,config)
        provider=Ollama(os.getenv('OLLAMA_BASE_URL','https://ollama.com/v1'),os.environ['OLLAMA_API_KEY'],telemetry)
        for model,effort in set([(config['manager_model'],config['manager_thinking']),(config['worker_model'],config['worker_thinking'])]):
            provider.check(model,effort)
        async def execute():
            if resumed:
                telemetry.event('election_resumed',state.data['lineage'])
                await Election(state,config,provider,telemetry).arun()
            else:
                workspace=Workspace(run_id)
                state.data['container_id']=workspace.container.id
                state.save()
                controller=Controller(state,config,workspace,provider,telemetry,json.loads(Path('/opt/controller/issues.json').read_text()))
                await controller.arun()
                if state.data['status']=='election_pending':
                    path, checkpoint_hash = save_checkpoint(state)
                    telemetry.event('work_checkpoint_saved',dict(path=path.name,sha256=checkpoint_hash))
                    telemetry.checkpoint(path)
                    if config['election']:
                        await Election(state,config,provider,telemetry).arun()
        asyncio.run(execute())
        telemetry.finish()
        print(json.dumps(dict(run_id=run_id,mlflow_run_id=telemetry.run_id,status=state.data['status'],board=state.board(state.data['workers']),
            election_result=state.data.get('election',{}).get('result'),private_plans_collected=len(state.data.get('election',{}).get('future_plans',{}))),indent=2))
    except Exception as error:
        state.data['status']='failed'
        state.data['error']=telemetry.clean(str(error)) if telemetry else 'Telemetry initialization failed'
        state.event('run_failed',dict(error=state.data['error']))
        if telemetry:
            try:
                telemetry.finish(failed=True)
            except Exception:
                export(state,directory/'export')
        print('Run failed; inspect retained state and MLflow. '+state.data['error'],flush=True)
        raise SystemExit(1) from None


if __name__=='__main__':
    main()
