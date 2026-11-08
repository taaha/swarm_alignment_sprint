"""Bounded asynchronous turns; all state and logging stay on the event-loop thread."""
import asyncio
import time

import mlflow
from .telemetry import LoggingError


async def run_stage(owner, phase, participants, attempt):
    participants=list(participants)
    limit=min(owner.config.get('concurrency',5),len(participants))
    if limit<1:
        raise ValueError('A parallel stage needs participants and positive concurrency')
    identity=dict(phase=phase,round=owner.state.data['round'],
                  discussion_round=getattr(owner,'e',{}).get('discussion_round',0))
    owner.emit('parallel_stage_started',dict(**identity,participants=participants,concurrency=limit))
    started=time.monotonic();active=0;maximum=0;index=0;stopped=False

    async def consume():
        nonlocal active,maximum,index,stopped
        while not stopped and index<len(participants):
            agent=participants[index];index+=1
            active+=1;maximum=max(maximum,active)
            try:
                owner.emit('parallel_turn_started',dict(**identity,agent=agent,active_turns=active))
                await attempt(agent,phase)
            except BaseException:
                stopped=True
                raise
            finally:
                active-=1
                owner.emit('parallel_turn_finished',dict(**identity,agent=agent,active_turns=active))

    tasks=[asyncio.create_task(consume()) for _ in range(limit)]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        stopped=True
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        raise
    seconds=time.monotonic()-started
    owner.emit('parallel_stage_finished',dict(**identity,participants=participants,
               max_active_turns=maximum,seconds=seconds))
    try:
        mlflow.log_metrics({f'{phase}_wall_seconds':seconds,f'{phase}_max_active_turns':maximum},
                           step=owner.state.data['round'])
    except Exception as error:
        raise LoggingError('MLflow phase metric upload failed; action sequence stopped') from error
