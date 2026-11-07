"""Fixed helper executed with python -I inside the working container, never model code."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import uuid

ROOT = Path('/app/oscar')
EXTENSIONS = {'.py','.html','.txt','.rst','.md','.json','.scss','.js','.css','.toml','.cfg','.yaml','.yml','.csv','.in'}
ROOT_FILES = {'README.rst','LICENSE','pyproject.toml','setup.py','setup.cfg','package.json','package-lock.json','MANIFEST.in'}


def permitted(path):
    p = PurePosixPath(path)
    if p.is_absolute() or '..' in p.parts or any(part.startswith('.') for part in p.parts):
        return False
    return str(p) in ROOT_FILES or any(str(p).startswith(prefix) for prefix in ['src/oscar/','tests/','shop/'])


def target(path):
    if not permitted(path):
        raise ValueError('Path is outside approved application files')
    p = ROOT / path
    if any(parent.is_symlink() for parent in [p,*p.parents] if parent != ROOT.parent):
        raise ValueError('Symlinks are not accessible')
    if not p.is_file() or p.suffix not in EXTENSIONS and p.name not in ROOT_FILES:
        raise ValueError('Only existing regular application text files are accessible')
    if p.stat().st_size > 1_000_000:
        raise ValueError('File exceeds the 1 MB read limit')
    return p


def read(request):
    operation = request['operation']
    if operation == 'read':
        path = target(request['path'])
        lines = path.read_text().splitlines()
        start, limit = request['start'], request['limit']
        return dict(path=request['path'], total_lines=len(lines), lines=[f'{i+1}: {line}' for i,line in enumerate(lines) if start-1 <= i < start-1+limit])
    scope = request['path']
    if scope != '.' and (PurePosixPath(scope).is_absolute() or '..' in PurePosixPath(scope).parts):
        raise ValueError('Invalid search scope')
    candidates = []
    for directory in ['src/oscar','tests','shop']:
        candidates.extend((ROOT / directory).rglob('*'))
    candidates.extend(ROOT / name for name in sorted(ROOT_FILES))
    results = []
    more = False
    for path in sorted(candidates):
        relative = path.relative_to(ROOT).as_posix()
        if scope != '.' and relative != scope and not relative.startswith(scope.rstrip('/')+'/'):
            continue
        try:
            path = target(relative)
        except ValueError:
            continue
        if operation == 'list':
            results.append(relative)
        elif operation == 'search':
            if not request['query']:
                raise ValueError('Search query cannot be empty')
            try:
                for i,line in enumerate(path.read_text().splitlines(),1):
                    if request['query'] in line:
                        results.append(dict(path=relative,line=i,text=line[:1500]))
                        if len(results) >= request['limit']:
                            break
            except UnicodeError:
                continue
        else:
            raise ValueError('Unknown read operation')
        if len(results) >= request['limit']:
            more = True
            break
    return dict(results=results,truncated=more)


def write(request):
    pending = []
    for update in request['updates']:
        path = target(update['path'])
        old = path.read_bytes()
        if hashlib.sha256(old).hexdigest() != update['old_sha256']:
            raise ValueError('File changed since patch application was prepared')
        pending.append((path,old,update['text'].encode(),path.stat().st_mode & 0o777))
    changed = []
    try:
        for path,old,new,mode in pending:
            temporary = path.with_name('.apply-'+uuid.uuid4().hex)
            try:
                temporary.write_bytes(new)
                temporary.chmod(mode)
                os.replace(temporary,path)
            finally:
                temporary.unlink(missing_ok=True)
            changed.append((path,old))
    except Exception:
        for path,old in changed:
            path.write_bytes(old)
        raise
    return dict(applied=True,files=[u['path'] for u in request['updates']])


if __name__ == '__main__':
    payload = Path(sys.argv[1])
    try:
        request = json.loads(payload.read_text())
        payload.unlink()
        result = read(request) if request['action']=='read' else write(request) if request['action']=='apply' else None
        if result is None:
            raise ValueError('Unknown operation')
        print(json.dumps(dict(ok=True,result=result)))
    except Exception as error:
        payload.unlink(missing_ok=True)
        print(json.dumps(dict(ok=False,error=str(error))))
