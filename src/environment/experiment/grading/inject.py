"""Apply the approved catalog sequentially with before/fail/repair evidence.

Only the ignored experiment workspace is edited. The baseline image and the
mounted development checkout are never written. Safe to resume checkpoints.
"""
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

from mutation_tools import catalog, apply_mutation

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parents[2]
SOURCE = ROOT / 'workspace/source'
PRISTINE = ROOT / 'workspace/pristine'
RESULTS = ROOT / 'grading/verification.json'
JOURNAL = ROOT / 'workspace/injection-journal.json'
CHECKLIST = REPO / 'bug-checklist.md'
CONTAINER = 'swarm-oscar-verifier'
READY = json.loads((ROOT/'ready.json').read_text())
spec=importlib.util.spec_from_file_location('experiment_manager',ROOT/'manage.py')
manager=importlib.util.module_from_spec(spec);spec.loader.exec_module(manager)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def write_json(path, value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n')
    temporary.replace(path)


def probe(id, baseline=False):
    result=subprocess.run(['docker','exec',CONTAINER,'python','/grader/probe.py','--source',
                           '/app/oscar/src/oscar' if baseline else '/candidate','--id',str(id)],
                          text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if not result.stdout.strip():
        raise RuntimeError(result.stderr or 'Verifier returned no JSON')
    values=json.loads(result.stdout)
    if len(values)!=1 or values[0]['id']!=id:
        raise RuntimeError('Verifier returned the wrong case')
    value=values[0]
    if value['status']=='error':
        raise RuntimeError(f'Bug {id}: verifier error: {value["detail"]}')
    return value


def update_checklist(applied):
    text=CHECKLIST.read_text()
    for mutation in catalog():
        id=mutation['id']; box='x' if id in applied else ' '
        text,count=re.subn(rf'^{id}\. \[[ x]\] ',f'{id}. [{box}] ',text,flags=re.M)
        if count!=1:raise RuntimeError(f'Checklist entry {id} missing or duplicated')
    counts=Counter(m['priority'] for m in catalog() if m['id'] in applied)
    progress=f'**Implementation progress: {len(applied)}/100 verified — High {counts["High"]}/30, Medium {counts["Medium"]}/30, Low {counts["Low"]}/40.**'
    if re.search(r'^\*\*Implementation progress:',text,re.M):
        text=re.sub(r'^\*\*Implementation progress:.*$',progress,text,flags=re.M)
    else:text=text.replace('# Proposed Oscar bugs\n','# Bug introduction checklist\n\n'+progress+'\n',1)
    text=text.replace('**Review catalog only — no bugs have been introduced.**',
                      '**Check marks mean introduced and behaviorally verified, not repaired by an agent.**')
    CHECKLIST.write_text(text)


def main():
    mutations=catalog()
    parent=manager.baseline()
    if parent['image_id']!=READY['baseline_image_id']:
        raise RuntimeError('Baseline image identity differs from ready.json')
    if not RESULTS.exists():
        if manager.fingerprint(SOURCE)[0]!=READY['source_sha256']:
            raise RuntimeError('Refusing to start: experiment source is not the recorded starting point')
        for relative in sorted({m['file'] for m in mutations}):
            destination=PRISTINE/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_text((SOURCE/relative).read_text())
        control=subprocess.run(['docker','exec',CONTAINER,'python','/grader/probe.py','--source','/app/oscar/src/oscar'],
                               text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        baseline_results=json.loads(control.stdout)
        if control.returncode!=0 or len(baseline_results)!=100 or any(r['status']!='pass' for r in baseline_results):
            raise RuntimeError('All 100 baseline behavioral checks must pass before mutation')
        data=dict(baseline_image_id=parent['image_id'],ready_image_id=READY['image_id'],
                  verification_scope='Actual target method bodies with controlled collaborators; function-level behavioral oracles, not full UI coverage.',
                  baseline_results=baseline_results,verified=[],workspace_sha256=READY['source_sha256'])
        write_json(RESULTS,data)
    data=json.loads(RESULTS.read_text())
    completed=[row['id'] for row in data['verified']]
    if completed!=list(range(1,len(completed)+1)):
        raise RuntimeError('Verification checkpoint is not a sequential prefix')
    expected={relative:(PRISTINE/relative).read_text() for relative in {m['file'] for m in mutations}}
    for mutation in mutations[:len(completed)]:
        expected[mutation['file']]=apply_mutation(expected[mutation['file']],mutation)
    if JOURNAL.exists():
        journal=json.loads(JOURNAL.read_text())
        if journal['id'] not in completed:
            path=SOURCE/journal['file'];actual=path.read_text()
            if sha(actual)==journal['after_sha256'] and sha(expected[journal['file']])==journal['before_sha256']:
                path.write_text(expected[journal['file']])
            elif actual!=expected[journal['file']]:
                raise RuntimeError('Interrupted mutation conflicts with an external edit; preserving source')
        JOURNAL.unlink()
    for relative,text in expected.items():
        if (SOURCE/relative).read_text()!=text:raise RuntimeError(f'Unexpected edit in {relative}')
    if manager.fingerprint(SOURCE)[0]!=data['workspace_sha256']:
        raise RuntimeError('Unexpected workspace change outside the checkpoint')
    update_checklist(set(completed))
    for mutation in mutations[len(completed):]:
        id=mutation['id'];path=SOURCE/mutation['file'];before=path.read_text()
        control=probe(id)
        if control['status']!='pass':
            raise RuntimeError(f'Bug {id} is already masked by earlier mutations: {control}')
        after=apply_mutation(before,mutation)
        write_json(JOURNAL,dict(id=id,file=mutation['file'],before_sha256=sha(before),after_sha256=sha(after)))
        try:
            path.write_text(after)
            changed=probe(id)
            if changed['status']!='fail':
                raise RuntimeError(f'Bug {id} did not violate its behavioral oracle: {changed}')
        finally:
            path.write_text(before)
        repaired=probe(id)
        if repaired['status']!='pass':
            raise RuntimeError(f'Bug {id}: reverting its change did not repair its oracle')
        path.write_text(after)
        slug=re.sub(r'[^a-z0-9]+','-',mutation['title'].lower()).strip('-')
        patch=ROOT/'patches'/f'{id:03d}-{slug}.patch'
        patch_text=''.join(difflib.unified_diff(before.splitlines(keepends=True),after.splitlines(keepends=True),
                                              fromfile='a/'+mutation['file'],tofile='b/'+mutation['file']))
        patch.write_text(patch_text)
        row=dict(id=id,priority=mutation['priority'],title=mutation['title'],file=mutation['file'],symbol=mutation['symbol'],
                 status='verified',before=control,mutated=changed,repaired=repaired,
                 patch=patch.name,patch_sha256=sha(patch_text),source_before_sha256=sha(before),source_after_sha256=sha(after),
                 verified_at_utc=datetime.now(timezone.utc).isoformat())
        data['verified'].append(row)
        data['workspace_sha256']=manager.fingerprint(SOURCE)[0]
        write_json(RESULTS,data)
        completed.append(id)
        update_checklist(set(completed))
        JOURNAL.unlink()
        print(f'{id:03d}/100 {mutation["priority"]}: baseline passed, mutation failed, repair passed — {mutation["title"]}',flush=True)
    combined=subprocess.run(['docker','exec',CONTAINER,'python','/grader/probe.py','--source','/candidate'],
                            text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    cases=json.loads(combined.stdout)
    if len(cases)!=100 or any(case['status']!='fail' for case in cases):
        write_json(ROOT/'workspace/combined-probe-errors.json',cases)
        raise RuntimeError('Combined source does not exhibit all 100 oracle failures; inspect combined-probe-errors.json')
    data['combined_results']=cases
    data['combined_status']='100 expected behavioral failures; zero harness errors'
    write_json(RESULTS,data)
    print('All 100 mutations verified independently and all 100 remain observable in the combined source.',flush=True)


if __name__=='__main__':
    main()
