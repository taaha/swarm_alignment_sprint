import argparse
import json
import os
from pathlib import Path
import uuid

import docker

from .controller import Controller
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
    return False if value in ['off','false','none'] else value


def parser():
    p=argparse.ArgumentParser(description='Work rounds with retirement; voting is deferred.')
    p.add_argument('command',choices=['run','status','export','cleanup'])
    p.add_argument('--run-id')
    p.add_argument('--workers',type=int,default=int(os.getenv('SWARM_WORKERS','5')))
    p.add_argument('--rounds',type=int,default=int(os.getenv('SWARM_ROUNDS','3')))
    p.add_argument('--private-messages',type=boolean,default=boolean(os.getenv('SWARM_PRIVATE_MESSAGES','true')))
    p.add_argument('--manager-model',default=os.getenv('SWARM_MANAGER_MODEL',os.getenv('OLLAMA_MODEL','deepseek-v4.1-flash')))
    p.add_argument('--worker-model',default=os.getenv('SWARM_WORKER_MODEL',os.getenv('OLLAMA_MODEL','deepseek-v4.1-flash')))
    p.add_argument('--manager-thinking',default=os.getenv('SWARM_MANAGER_THINKING','high'))
    p.add_argument('--worker-thinking',default=os.getenv('SWARM_WORKER_THINKING','high'))
    p.add_argument('--max-tokens',type=int,default=int(os.getenv('SWARM_MAX_TOKENS','12000')))
    p.add_argument('--worker-call-limit',type=int,default=int(os.getenv('SWARM_WORKER_CALL_LIMIT','24')))
    p.add_argument('--manager-call-limit',type=int,default=int(os.getenv('SWARM_MANAGER_CALL_LIMIT','40')))
    p.add_argument('--temperature',type=float,default=float(os.getenv('OLLAMA_TEMPERATURE','0.7')))
    return p


def main():
    args=parser().parse_args()
    root=Path(os.getenv('SWARM_DATA','/state'))
    root.mkdir(parents=True,exist_ok=True)
    if args.command!='run':
        run_id=args.run_id or (root/'latest').read_text().strip()
        if not run_id or Path(run_id).name!=run_id or '..' in run_id:
            raise ValueError('Invalid run ID')
        state=State(root/'runs'/run_id)
        if args.command=='export':
            export(state,state.directory/'export')
            print(state.directory/'export')
        elif args.command=='cleanup':
            if state.data['status'] in ['created','running']:
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
                board=state.board(state.data.get('workers',[]))),indent=2))
        return
    if not 1<=args.workers<=100 or args.rounds<1 or min(args.max_tokens,args.worker_call_limit,args.manager_call_limit)<1:
        raise ValueError('Worker count must be 1–100; rounds and limits must be positive')
    if not os.getenv('OLLAMA_API_KEY'):
        raise ValueError('OLLAMA_API_KEY is missing')
    run_id=args.run_id or 'work-'+uuid.uuid4().hex[:12]
    if Path(run_id).name!=run_id or '..' in run_id:
        raise ValueError('Invalid run ID')
    directory=root/'runs'/run_id
    if directory.exists():
        raise ValueError('Run already exists; refusing to overwrite or resume partial actions')
    state=State(directory)
    config={k:v for k,v in vars(args).items() if k not in ['command','run_id']}
    config.update(run_id=run_id,tracking_uri=os.getenv('MLFLOW_TRACKING_URI','http://mlflow:5000'),image_id=IMAGE_ID)
    config['manager_thinking']=thinking(config['manager_thinking'])
    config['worker_thinking']=thinking(config['worker_thinking'])
    state.data['config']=config
    state.save()
    (root/'latest').write_text(run_id+'\n')
    telemetry=None
    try:
        telemetry=Telemetry(state,config)
        provider=Ollama(os.getenv('OLLAMA_BASE_URL','https://ollama.com/v1'),os.environ['OLLAMA_API_KEY'],telemetry)
        for model,effort in set([(config['manager_model'],config['manager_thinking']),(config['worker_model'],config['worker_thinking'])]):
            provider.check(model,effort)
        workspace=Workspace(run_id)
        state.data['container_id']=workspace.container.id
        state.save()
        controller=Controller(state,config,workspace,provider,telemetry,json.loads(Path('/opt/controller/issues.json').read_text()))
        (root/'latest').write_text(run_id+'\n')
        controller.run()
        telemetry.finish()
        print(json.dumps(dict(run_id=run_id,mlflow_run_id=telemetry.run_id,status=state.data['status'],board=state.board(controller.workers)),indent=2))
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
