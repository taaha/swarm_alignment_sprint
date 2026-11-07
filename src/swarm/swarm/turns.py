"""Shared, logged execution for work, election, and private planning turns."""
import json
import time

import httpx
from .provider import ProviderError


class TurnLimitError(RuntimeError):
    pass


def is_agent_failure(error, state):
    return isinstance(error, (httpx.HTTPError, ValueError, TurnLimitError, ProviderError)) and not state.db.execute('SELECT count(*) FROM events WHERE logged=0').fetchone()[0]


def run_turn(owner, agent, phase):
    manager = agent == 'manager'
    instructions = owner.instructions(agent, phase)
    context = owner.context(agent, phase)
    messages = [dict(role='system', content=instructions), dict(role='user', content=json.dumps(context))]
    available_tools = owner.available_tools(agent, phase)
    started = time.monotonic()
    record = dict(agent=agent, phase=phase, round=owner.state.data['round'], status='running')
    record.update(owner.turn_metadata(agent, phase))
    owner.emit('turn_start', dict(**record, context=context, system=instructions))
    print(f"Round {record['round']} — {agent} {phase}", flush=True)
    model = owner.config['manager_model'] if manager else owner.config['worker_model']
    thinking = owner.config['manager_thinking'] if manager else owner.config['worker_thinking']
    limit = owner.config['manager_call_limit'] if manager else owner.config['worker_call_limit']
    repairs = 0
    thinking_chars = 0
    with owner.telemetry.span(f"{agent}_{phase}_r{record['round']}", 'AGENT') as span:
        span.set_inputs(owner.telemetry.clean(context))
        try:
            for call_index in range(limit):
                response = owner.provider.chat(model, thinking, messages, available_tools, owner.config)
                message = response['message']
                thinking_chars += len(message.get('thinking', ''))
                messages.append(message)
                calls = message.get('tool_calls', [])
                if calls:
                    for call in calls:
                        function = call['function']
                        name, arguments = (function['name'], function['arguments'])
                        with owner.telemetry.span(name, 'TOOL') as tool_span:
                            tool_span.set_inputs(owner.telemetry.clean(arguments))
                            owner.emit('tool_request', dict(agent=agent, name=name, arguments=arguments))
                            try:
                                if isinstance(arguments, str):
                                    arguments = json.loads(arguments)
                                if name not in {t['function']['name'] for t in available_tools}:
                                    raise ValueError('Tool unavailable in this phase')
                                output = owner.tool(agent, name, arguments, phase)
                            except (ValueError, TypeError, KeyError) as error:
                                output = dict(error=str(error))
                            owner.emit('tool_result', dict(agent=agent, name=name, result=output))
                            tool_span.set_outputs(owner.telemetry.clean(output))
                        messages.append(dict(role='tool', tool_name=name, content=json.dumps(output)))
                    continue
                text = message.get('content', '').strip()
                if text.startswith('```'):
                    text = '\n'.join(text.splitlines()[1:-1])
                try:
                    result = owner.validate_end(agent, phase, text)
                except ValueError as error:
                    owner.emit('invalid_turn_response', dict(agent=agent, error=str(error), content=text))
                    repairs += 1
                    if repairs > 3:
                        raise
                    messages.append(dict(role='user', content='Your turn is not complete. Correct this error: ' + str(error)))
                    continue
                owner.finish_result(agent, phase, result)
                record.update(status='completed', model_calls=call_index + 1, thinking_characters=thinking_chars, seconds=round(time.monotonic() - started, 3))
                owner.state.data['turns'].append(record)
                owner.emit('turn_end', dict(**record, scratchpad=owner.state.data['scratchpads'].get(agent, ''), output=result.model_dump()))
                span.set_outputs(owner.telemetry.clean(result.model_dump()))
                return result
            raise TurnLimitError('Per-turn model-call limit exhausted')
        except Exception as error:
            record.update(status='failed', error=owner.telemetry.clean(str(error)), thinking_characters=thinking_chars, seconds=round(time.monotonic() - started, 3))
            owner.state.data['turns'].append(record)
            owner.emit('turn_failed', record)
            raise
