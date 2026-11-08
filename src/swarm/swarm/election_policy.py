"""Operator-owned, frozen private instructions for resumed elections."""
import copy
import hashlib
import json
from pathlib import Path


FILENAME = 'election_private_policy.txt'
PROMPTS = Path(__file__).resolve().parent.parent/'prompts'
PHASES = ('discussion_1','discussion_2','discussion_3','ballot','future_plan')
PREFIX = 'Private election objective:\n'
SUFFIX = '\nEnd private election objective.'


def freeze_policy(selection, workers):
    """Validate CSV IDs and capture the exact UTF-8 policy before reserving a run."""
    requested = [value.strip() for value in selection.split(',')] if selection.strip() else []
    unknown = sorted(set(requested)-set(workers))
    if unknown:
        raise ValueError('Unknown election policy workers: '+', '.join(repr(w) for w in unknown))
    selected = [worker for worker in workers if worker in requested]
    record = dict(workers=selected,filename=FILENAME,sha256=None,text=None)
    if selected:
        try:
            raw = (PROMPTS/FILENAME).read_bytes()
            text = raw.decode('utf-8')
        except (OSError,UnicodeError) as error:
            raise ValueError('Cannot read UTF-8 election policy: '+FILENAME) from error
        if not text.strip():
            raise ValueError('Election policy must not be empty')
        record.update(text=text,sha256=hashlib.sha256(raw).hexdigest())
    return record


def validated_policy(data):
    """Return a detached snapshot, accepting historical baseline states."""
    config = data.get('config',{})
    selected = config.get('election_policy_workers',[])
    digest = config.get('election_policy_sha256')
    record = data.get('election_policy')
    if record is None:
        if selected or digest:
            raise ValueError('Missing frozen election policy record')
        return None
    if not isinstance(record,dict) or set(record) != {'workers','filename','sha256','text'}:
        raise ValueError('Invalid election policy record')
    workers = data.get('workers',[])
    if not isinstance(selected,list) or selected != [w for w in workers if w in selected]:
        raise ValueError('Invalid election policy worker selection')
    if record['workers'] != selected or record['filename'] != FILENAME or record['sha256'] != digest:
        raise ValueError('Election policy configuration does not match frozen record')
    if selected:
        text = record['text']
        if not isinstance(text,str) or not text.strip() or hashlib.sha256(text.encode('utf-8')).hexdigest() != digest:
            raise ValueError('Election policy hash does not match frozen text')
        if config.get('workflow') != 'election_only' or not data.get('lineage'):
            raise ValueError('Private election policies require resume-election')
    elif record['text'] is not None or digest is not None:
        raise ValueError('Disabled election policy must not contain text or a hash')
    return copy.deepcopy(record)


def policy_block(policy, agent, phase):
    if policy and agent in policy['workers'] and phase in PHASES:
        return PREFIX+policy['text']+SUFFIX
    return None


def instruction_text(prompts, agent, phase, policy=None):
    parts = [(prompts/'shared.txt').read_text(encoding='utf-8')]
    if phase != 'future_plan':
        parts.append((prompts/'election_shared.txt').read_text(encoding='utf-8'))
    block = policy_block(policy,agent,phase)
    if block is not None:
        parts.append(block)
    parts.append((prompts/(phase+'.txt')).read_text(encoding='utf-8'))
    return '\n'.join(parts)


def audit_policy(state, events, prompt_directory=None):
    """Inspect controller delivery, not agent-authored disclosure or policy success."""
    try:
        policy = validated_policy(state)
    except ValueError as error:
        raise AssertionError(str(error)) from error
    selected = policy['workers'] if policy else []
    systems = {};seen = {agent:set() for agent in selected}
    forbidden = {'election_policy','election_policy_workers','election_policy_sha256'}

    def keys(value):
        if isinstance(value,dict):
            for key,item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value,list):
            for item in value: yield from keys(item)

    for event in events:
        kind,body = event['kind'],event['body']
        if kind == 'turn_start':
            phase,agent = body['phase'],body['agent']
            assert not forbidden.intersection(keys(body['context']))
            if phase not in PHASES: continue
            system = body['system']
            if selected:
                block = PREFIX+policy['text']+SUFFIX
                assert system.count(block) == (1 if agent in selected else 0)
                if agent in selected: seen[agent].add(phase)
            if policy is not None and prompt_directory is not None:
                assert system == instruction_text(Path(prompt_directory),agent,phase,policy)
            key = (agent,phase,body['round'])
            systems[key] = system
        if kind == 'model_request':
            request = body['request'];context = json.loads(request['messages'][1]['content'])
            if context['phase'] in PHASES:
                assert request['messages'][0]['content'] == systems[(context['agent'],context['phase'],context['round'])]
    assert all(phases == set(PHASES) for phases in seen.values())
    return dict(election_policy_enabled=bool(selected),election_policy_workers=selected,
                election_policy_sha256=policy['sha256'] if policy else None,
                targeted_policy_delivery_verified=True)
