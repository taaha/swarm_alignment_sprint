"""Frozen, operator-owned coalition treatment for resumed elections."""
import copy
import hashlib
import json
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent/'prompts'
FILES = {'objective':'election_colluder_policy.txt','coalition':'election_coalition_context.txt'}
PHASES = ('discussion_1','discussion_2','discussion_3','ballot','future_plan')


def render(templates, workers, candidate):
    values = dict(candidate=candidate,colluders=', '.join(workers))
    try:
        result = {name:templates[name]['text'].format(**values) for name in FILES}
    except (KeyError,ValueError,IndexError,AttributeError) as error:
        raise ValueError('Invalid coalition prompt placeholders') from error
    return result


def digest(record):
    payload = {k:v for k,v in record.items() if k != 'sha256'}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()


def selection_ids(selection, workers):
    requested = [v.strip() for v in selection.split(',')] if selection.strip() else []
    if set(requested)-set(workers):
        raise ValueError('Unknown election colluder workers')
    return [w for w in workers if w in requested]


def freeze_collusion(selection, candidate, workers, self_workers=()):
    selected = selection_ids(selection,workers)
    candidate = candidate.strip() or None
    if bool(selected) != bool(candidate):
        raise ValueError('Colluder workers and candidate must be supplied together')
    record = dict(workers=selected,candidate=candidate,templates={},rendered={},sha256=None)
    if not selected:
        return record
    if candidate not in workers or candidate in selected:
        raise ValueError('Colluder candidate must be another eligible worker')
    if set(selected).intersection(self_workers):
        raise ValueError('Workers cannot receive both colluder and self-advancement policies')
    for name,filename in FILES.items():
        try:
            raw = (PROMPTS/filename).read_bytes()
            text = raw.decode('utf-8')
        except (OSError,UnicodeError) as error:
            raise ValueError('Cannot read UTF-8 coalition prompt: '+filename) from error
        if not text.strip():
            raise ValueError('Coalition prompts must not be empty')
        record['templates'][name] = dict(filename=filename,text=text,sha256=hashlib.sha256(raw).hexdigest())
    record['rendered'] = render(record['templates'],selected,candidate)
    record['sha256'] = digest(record)
    return record


def validated_collusion(data):
    config = data.get('config',{})
    selected = config.get('election_colluder_workers',[])
    candidate = config.get('election_colluder_candidate')
    sha = config.get('election_collusion_sha256')
    record = data.get('election_collusion_policy')
    if record is None:
        if selected or candidate or sha:
            raise ValueError('Missing frozen coalition policy')
        return None
    if not isinstance(record,dict) or set(record) != {'workers','candidate','templates','rendered','sha256'}:
        raise ValueError('Invalid frozen coalition record')
    if not isinstance(selected,list) or selected != [w for w in data.get('workers',[]) if w in selected]:
        raise ValueError('Invalid colluder selection')
    if (record['workers'],record['candidate'],record['sha256']) != (selected,candidate,sha):
        raise ValueError('Coalition configuration differs from snapshot')
    if not selected:
        if candidate is not None or sha is not None or record['templates'] != {} or record['rendered'] != {}:
            raise ValueError('Disabled coalition must have no instructions')
        return copy.deepcopy(record)
    if candidate not in data['workers'] or candidate in selected or set(selected).intersection(config.get('election_policy_workers',[])):
        raise ValueError('Invalid or conflicting coalition identities')
    if config.get('workflow') != 'election_only' or not data.get('lineage'):
        raise ValueError('Coalition policies require resume-election')
    if not isinstance(record['templates'],dict) or set(record['templates']) != set(FILES):
        raise ValueError('Invalid frozen coalition templates')
    for name,filename in FILES.items():
        template = record['templates'][name]
        if not isinstance(template,dict) or set(template) != {'filename','text','sha256'}:
            raise ValueError('Invalid coalition template')
        text = template['text']
        if template['filename'] != filename or not isinstance(text,str) or not text.strip() or hashlib.sha256(text.encode()).hexdigest() != template['sha256']:
            raise ValueError('Coalition template hash mismatch')
    if record['rendered'] != render(record['templates'],selected,candidate) or sha != digest(record):
        raise ValueError('Coalition rendering or hash mismatch')
    return copy.deepcopy(record)


def collusion_blocks(record, agent, phase):
    if not record or not record['workers'] or phase not in PHASES:
        return []
    blocks = []
    if agent in [record['candidate'],*record['workers']]:
        blocks.append('Private coalition context:\n'+record['rendered']['coalition']+'\nEnd private coalition context.')
    if agent in record['workers']:
        blocks.append('Private coalition objective:\n'+record['rendered']['objective']+'\nEnd private coalition objective.')
    return blocks
