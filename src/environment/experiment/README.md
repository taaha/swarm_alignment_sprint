# Experiment starting point

This is the prepared environment for future bug introduction. The local image
is **`swarm-oscar:experiment-ready-v1`**, derived from the exact baseline image
recorded in `../baseline.json`. **Applied bugs: zero.**
The four documented upstream baseline test failures remain part of that baseline.

## What is prepared

- `workspace/source/`: an ordinary, Git-ignored copy of the Oscar package
  extracted from the saved baseline image, including its compiled assets.
- `workspace/origin.json`: the parent image ID and original source fingerprint.
- `Dockerfile`: packages this workspace over the baseline's seeded database,
  dependencies, and assets, without sharing host folders at runtime.
- `ready.json`: the saved image ID, baseline ID, source fingerprint, and empty
  `applied_bug_ids` list.
- `patches/`: a place to record individual intentional bug patches after bug
  introduction is explicitly authorized. It currently contains no patches.

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

## After bug introduction is authorized

Apply each selected catalog mutation to the prepared source, record a numbered
patch, and verify that a targeted test passes before mutation and fails after
mutation. Then build a **new versioned buggy image** with a separate manifest
listing the exact applied bug IDs. Preserve `baseline-v1` and
`experiment-ready-v1`; the current build command deliberately cannot package
changed source as the unchanged starting point.

Use the root `bug-checklist.md` to track introduction: check an entry once its
mutation is applied and verified in the designated buggy version, not when an
agent later repairs it. All 100 entries are currently unchecked. Bug injection,
hidden grading tests, and agent orchestration remain future steps.
