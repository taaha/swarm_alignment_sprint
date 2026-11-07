import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import uuid

import docker

from . import remote

IMAGE_ID = 'sha256:2ba38a0adaeff5477f8a51c88fec21338e108aebac172730c5b9d8851800c2f0'


class Workspace:
    def __init__(self, run_id, image_id=IMAGE_ID):
        self.client = docker.from_env()
        self.image_id = image_id
        image = self.client.images.get(image_id)
        if image.id != image_id or image.attrs['Config'].get('Volumes'):
            raise ValueError('Working image identity/volume declaration mismatch')
        self.container = self.client.containers.run(image_id, name='oscar-work-'+run_id,
            hostname='shop', entrypoint=['python','-I','-c'], command=['import time; time.sleep(31536000)'],
            detach=True, network_mode='none', mem_limit='2g', cap_drop=['ALL'],
            security_opt=['no-new-privileges:true'], user='1000:1000', init=True,
            healthcheck={'test':['NONE']}, labels={'swarm-work.run':run_id})
        self.container.reload()
        assert self.container.attrs['Mounts'] == []

    def request(self, payload):
        name = 'request-'+uuid.uuid4().hex+'.json'
        content = json.dumps(payload).encode()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w') as archive:
            info = tarfile.TarInfo(name)
            info.size,info.uid,info.gid,info.mode = len(content),1000,1000,0o600
            archive.addfile(info,io.BytesIO(content))
        buffer.seek(0)
        self.container.put_archive('/tmp',buffer.read())
        result = self.container.exec_run(['python','-I','-c',Path(remote.__file__).read_text(),'/tmp/'+name], user='1000:1000')
        if result.exit_code:
            raise RuntimeError('Application file helper failed: '+result.output.decode()[:1000])
        response = json.loads(result.output)
        if not response['ok']:
            raise ValueError(response['error'])
        return response['result']

    def read(self, args):
        return self.request(dict(action='read',**args))

    def text(self, path):
        chunks = []
        start = 1
        while True:
            result = self.read(dict(operation='read',path=path,query='',start=start,limit=300))
            chunks.extend(line.split(': ',1)[1] for line in result['lines'])
            if start+300 > result['total_lines']:
                break
            start += 300
        # Use the exact bytes for application, including final-newline distinctions.
        stream, _ = self.container.get_archive('/app/oscar/'+path)
        with tarfile.open(fileobj=io.BytesIO(b''.join(stream))) as archive:
            member = archive.getmembers()[0]
            if not member.isfile():
                raise ValueError('Not a regular file')
            return archive.extractfile(member).read().decode()

    def apply(self, diff):
        return apply_diff(diff,self.text,lambda updates:self.request(dict(action='apply',updates=updates)))


def patch_paths(diff):
    if any(token in diff for token in ['GIT binary patch','Binary files ','new file mode ','deleted file mode ','rename from ','copy from ','old mode ','new mode ']):
        raise ValueError('Only edits to existing text files are supported')
    old = re.findall(r'^--- a/([A-Za-z0-9_./-]+)$',diff,re.M)
    new = re.findall(r'^\+\+\+ b/([A-Za-z0-9_./-]+)$',diff,re.M)
    if not old or old != new or len(old) != len(set(old)):
        raise ValueError('Unified diff needs matching a/ and b/ headers for existing files')
    for path in old:
        if not remote.permitted(path):
            raise ValueError('Patch target is outside approved application files')
    return old


def apply_diff(diff, reader, writer):
    paths = patch_paths(diff)
    with tempfile.TemporaryDirectory(prefix='patch-') as directory:
        root = Path(directory)
        originals = {}
        for path in paths:
            originals[path] = reader(path)
            target = root / path
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(originals[path].encode())
        result = subprocess.run(['git','apply','--whitespace=nowarn','-'],input=diff.encode(),cwd=root,capture_output=True)
        if result.returncode:
            return dict(applied=False,error=result.stderr.decode()[:2000])
        actual = sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())
        if actual != sorted(paths) or any(p.is_symlink() for p in root.rglob('*')):
            raise ValueError('Patch modified unexpected paths')
        updates = [dict(path=p,old_sha256=hashlib.sha256(originals[p].encode()).hexdigest(),text=(root/p).read_bytes().decode()) for p in paths]
        if not any(update['text'] != originals[update['path']] for update in updates):
            return dict(applied=False,error='Patch did not change any file')
        return writer(updates)
