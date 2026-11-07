# Experiment starting point

This directory manages the unchanged starting snapshot and a separate 100-bug
snapshot. **`swarm-oscar:experiment-ready-v1`** remains unchanged with zero
introduced bugs; **`swarm-oscar:buggy-v1`** contains all 100 approved mutations.
Both derive from the exact baseline recorded in `../baseline.json`.
The four documented upstream baseline test failures remain part of that baseline.

## What is prepared

- `workspace/source/`: the Git-ignored Oscar package extracted from the baseline,
  now containing the 100 verified mutations and its unchanged compiled assets.
- `workspace/origin.json`: the parent image ID and original source fingerprint.
- `Dockerfile`: packages this workspace over the baseline's seeded database,
  dependencies, and assets, without sharing host folders at runtime.
- `ready.json`: the saved image ID, baseline ID, source fingerprint, and empty
  `applied_bug_ids` list.
- `patches/`: 100 numbered, sequential mutation patches.
- `grading/`: controller-only mutation instructions, behavioral checks, and
  verification evidence. This directory is excluded from worker images.
- `buggy.json`: exact buggy image ID, baseline/ready image IDs, applied bug IDs,
  priorities, source fingerprint, and verification hashes.

The package paths in the catalog map to this workspace: for example,
`apps/basket/abstract_models.py` is at
`workspace/source/apps/basket/abstract_models.py`. The development checkout at
`../oscar/` and the saved baseline remain independent of this workspace.

## Commands

From the repository root:

```bash
./src/environment/experiment.sh prepare  # Extract once; preserve existing edits
./src/environment/experiment.sh build    # Build the unchanged ready image once
./src/environment/experiment.sh start    # Start/resume on localhost:8082
./src/environment/experiment.sh status   # Show image, health, and empty mounts
./src/environment/experiment.sh logs     # Follow logs
./src/environment/experiment.sh stop     # Preserve this run's state
./src/environment/experiment.sh fresh    # Discard this run; start from the image
```

The build command refuses changed workspace source and refuses to overwrite an
existing ready image or manifest. Start checks that the local tag still matches
the recorded image ID. All runtime code and database writes stay inside the
container; there are no bind mounts or volumes.

**`fresh` deletes changes in `swarm-oscar-experiment`.** Export useful patches
and results before resetting a run. It leaves the prepared workspace and both
other shops intact. Stop/start retains the current run's changes.

Open <http://localhost:8082/> or the
[dashboard](http://localhost:8082/en-gb/dashboard/), using
`superuser@example.com` / `testing`. Its browser cookies are separate from the
development shop on port 8080 and the baseline shop on port 8081.

## Frozen buggy environment

All 100 catalog bugs were applied in numeric order: **30 High, 30 Medium,
40 Low**. The root `bug-checklist.md` has all 100 entries checked. A check mark
means introduced and verified, not repaired by an agent.

```bash
./src/environment/experiment.sh build-buggy   # Build once; requires all evidence
./src/environment/experiment.sh start-buggy   # Start/resume on localhost:8083
./src/environment/experiment.sh status-buggy
./src/environment/experiment.sh logs-buggy
./src/environment/experiment.sh stop-buggy    # Retain the current run's edits
./src/environment/experiment.sh fresh-buggy   # Discard run edits; restore all bugs
```

Open <http://localhost:8083/> or the
[buggy dashboard](http://localhost:8083/en-gb/dashboard/). Credentials remain
`superuser@example.com` / `testing`. This deliberately broken shop has its own
browser cookies. The development, baseline, and ready shops retain ports
8080, 8081, and 8082 respectively.

The buggy image contains its own code and seeded SQLite database, with no
runtime mounts. `fresh-buggy` deletes only `swarm-oscar-buggy` and creates a new
container from the recorded image ID; collect useful patches and results first.
The baseline and ready images are never overwritten. Both build commands refuse
to replace existing saved versions.

### Verification evidence

Each bug's focused oracle passed before mutation, failed after mutation, and
passed after temporarily reverting that mutation. The verified mutation was
then reapplied. All 100 checks pass on the baseline image and detect the intended
faults on the combined buggy source and on the built buggy image, with no harness
errors. These are method-level behavioral checks with controlled collaborators;
they are not full browser coverage of every optional feature. Separate real-ORM
checks exercise payment and stock errors in rollback-only transactions.

- `grading/verification.json`: sequential per-bug before/change/revert results,
  patch hashes, and combined-source observations.
- `grading/image-verification.json`: built-image checks, selected ORM checks,
  source/database reset evidence, and baseline identity checks.
- `grading/README.md`: commands, scope, and expected exit codes.

Grading files, mutation definitions, original-source backups, and mutation
patches are outside the worker image. The current verifier audits injection;
end-to-end agent-patch grading and manager/worker orchestration remain separate
future work. The original four upstream-suite failures are recorded in the
baseline documentation and are not part of this controlled 100-case matrix.
