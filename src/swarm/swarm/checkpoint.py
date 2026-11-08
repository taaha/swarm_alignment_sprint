"""Immutable retirement checkpoints and read-only sources for independent elections."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

VERSION = 1
NAME = 'work_checkpoint.json'


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))+'\n').encode()


def valid_run_id(value):
    if not value or Path(value).name != value or '..' in value or value in ['.', '/']:
        raise ValueError('Invalid run ID')
    return value


def validate_work(data):
    if data.get('status') != 'election_pending' or 'election' in data:
        raise ValueError('Source must be retired work state without election history')
    if data.get('assignments') or not data.get('completed_rounds') or data['completed_rounds'] != data['config']['rounds']:
        raise ValueError('Source work phase is not complete')
    workers = data.get('workers', [])
    if not workers or len(set(workers)) != len(workers) or len(workers) != data['config']['workers']:
        raise ValueError('Invalid checkpoint workers')
    if not any(m.get('kind') == 'manager_retired' for m in data['public']):
        raise ValueError('Source has no manager retirement')
    if any(t['phase'] not in ['initial', 'work', 'review'] for t in data['turns']):
        raise ValueError('Checkpoint contains election turns')


def envelope(data):
    validate_work(data)
    payload = copy.deepcopy(data)
    return dict(version=VERSION, state_sha256=hashlib.sha256(canonical(payload)).hexdigest(), state=payload)


def decode(raw):
    value = json.loads(raw)
    if value.get('version') != VERSION or hashlib.sha256(canonical(value['state'])).hexdigest() != value.get('state_sha256'):
        raise ValueError('Invalid checkpoint version or hash')
    validate_work(value['state'])
    return value


def save_checkpoint(state):
    raw = canonical(envelope(state.data))
    path = state.directory / NAME
    # Publish complete bytes atomically, without replacing an existing checkpoint.
    with tempfile.NamedTemporaryFile(dir=state.directory, delete=False) as file:
        temporary = Path(file.name)
        file.write(raw)
        os.fchmod(file.fileno(), 0o644)
        file.flush()
        os.fsync(file.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError('Refusing to overwrite a different work checkpoint')
    finally:
        temporary.unlink()
    return path, hashlib.sha256(raw).hexdigest()


def load_source(root, run_id):
    directory = Path(root) / 'runs' / valid_run_id(run_id)
    if not directory.is_dir():
        raise ValueError('Source run does not exist')
    path = directory / NAME
    if path.is_file():
        raw = path.read_bytes()
        value = decode(raw)
    else:
        database = directory / 'state.sqlite'
        if not database.is_file():
            raise ValueError('Source has no checkpoint or saved state')
        with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as db:
            row = db.execute('SELECT body FROM snapshot WHERE id=1').fetchone()
        if row is None:
            raise ValueError('Source has no saved state')
        value = envelope(json.loads(row[0]))
        raw = canonical(value)
    if value['state']['config']['run_id'] != run_id:
        raise ValueError('Checkpoint source identity mismatch')
    return value['state'], raw, hashlib.sha256(raw).hexdigest()


def election_state(source, source_id, digest):
    data = copy.deepcopy(source)
    data.pop('error', None)
    data.pop('mlflow_run_id', None)
    data['container_id'] = None
    data['lineage'] = dict(source_run_id=source_id, checkpoint_sha256=digest,
                           source_mlflow_run_id=source.get('mlflow_run_id'),
                           source_container_id=source.get('container_id'),
                           inherited_work_turns=len(source['turns']))
    return data
