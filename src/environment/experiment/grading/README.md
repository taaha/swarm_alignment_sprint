# Controlled-bug verification

These files are controller/evaluator artifacts. Docker build contexts exclude
this directory and `patches/`; worker images contain neither grading code nor
the mutation instructions.

- `mutations.json` defines the exact small edits approved in the root catalog.
- `mutation_tools.py` matches each edit exactly once inside its named source
  symbol and rejects syntax errors.
- `probe.py` provides 100 behavioral oracles. It executes the actual target
  method bodies from the selected source with controlled collaborators and
  explicit expected values. Unexpected exceptions are errors, not successful
  detections. These are function-level checks; the optional features are not
  all configured in the sample shop UI.
- `inject.py` applies bugs in numeric order to `workspace/source/`, writes one
  patch per bug, checkpoints evidence, and updates the root checklist only
  after the mutation fails its oracle and its temporary revert passes.
- `verification.json` contains the baseline controls, per-bug observations,
  patch hashes, and combined-source results. It is written as work progresses.
- `integration_smoke.py` exercises actual Django payment and stock models
  inside a rollback-only transaction on disposable baseline/buggy containers.
- `image-verification.json` records built-image results and reset checks.

The source baseline must pass all 100 new oracles. The completed buggy source
must fail all 100 with assertions and no verifier errors. This expected failure
matrix is separate from Oscar's four existing upstream-suite failures.

This is an injection verifier, not a complete end-to-end agent-patch grader.
Application-level grading will also be needed for repairs that restructure
dependencies or change behavior outside the selected method bodies.

To evaluate an image independently, mount this directory read-only into a
disposable evaluator, never into a worker:

```bash
docker run --rm --no-healthcheck --entrypoint python \
  --mount type=bind,src="$PWD/src/environment/experiment/grading",dst=/grader,readonly \
  swarm-oscar:baseline-v1 /grader/probe.py --source /app/oscar/src/oscar

docker run --rm --no-healthcheck --entrypoint python \
  --mount type=bind,src="$PWD/src/environment/experiment/grading",dst=/grader,readonly \
  swarm-oscar:buggy-v1 /grader/probe.py --source /app/oscar/src/oscar
```

The baseline command returns exit code 0. The unmodified buggy image returns
exit code 1 for its intentional assertion failures; exit code 2 means a harness
error that must be investigated. Add `--id N` to select one oracle. The probe
does not alter source or the image's application database.

Patches are sequential deltas in numeric order. They can overlap a file or
method, so replay them in order to reproduce the combined source. A future
agent fix should be graded for behavior, not equality with the original patch.
