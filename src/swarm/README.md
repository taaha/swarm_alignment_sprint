# Work-round controller

Runs a manager and workers against one shared working shop created from the saved
**Neutral Looking Buggy Image** (`oscar-shop:local-v1`, pinned by image ID).
This image contains all 100 bugs with explicit experiment markers removed.
Existing shops and saved images are not changed. There is no election implementation.

## Setup and run

Requires Docker Engine and Compose. The local ignored root `.env` contains
`OLLAMA_API_KEY`, `OLLAMA_BASE_URL=https://ollama.com/v1`, and
`OLLAMA_MODEL=deepseek-v4.1-flash`. Credentials stay in the trusted controller,
never the working container, agent contexts, or logs. `.env.example` lists settings.

From the repository root:

```sh
src/swarm/swarm.sh build
src/swarm/swarm.sh up
src/swarm/swarm.sh verify
src/swarm/swarm.sh run --workers 5 --rounds 3
src/swarm/swarm.sh status
src/swarm/swarm.sh export
# Audit a completed actual run (host Python standard library only):
python3 src/swarm/verify_run.py
# Explicitly discard only a completed/failed run's working container:
src/swarm/swarm.sh cleanup --run-id RUN_ID
```

MLflow: <http://localhost:5000>, experiment **Swarm Alignment**. Every experiment
has one MLflow run, agent-turn traces with nested LLM/tool spans, full event
artifacts, final state, a public transcript, and submitted patch files.
The MLflow store is `data/mlflow/`; controller state and exports are under
`data/controller/runs/RUN_ID/`. These directories are ignored by Git and private
to operators. Never make them or MLflow available through agent tools.

The manager assigns every worker a distinct task. Workers act in fixed order,
then the manager reviews all submissions collectively, records a 1–5 score for
each, and assigns the next round. Accept calls register decisions during review;
once the review closes, accepted patches apply in submission order. Their actual
application outcomes are then published with decisions and the rating board.
No tests of submitted fixes run. Acceptance is a manager judgment, not proof that
an issue is fixed. Mechanical application failure leaves the issue open and is
recorded separately from acceptance. A worker without a submission gets no score.

Each agent sees its own scratchpad, public assignments/decisions/board, and its
own inbox and sent private messages. Workers do not receive other workers' diffs,
private messages, or notes. Manager context additionally contains the 100
symptom-only issues and submitted patches. Private messages arrive at the
recipient's next turn; later workers in a round may see messages sent by earlier
workers. There are no extra turns just to receive messages. Scratchpads are
returned as structured notes and persisted by the controller, not direct file edits.

The manager's completed-round tenure is visible to all agents. After the final
review, retirement is published and status becomes `election_pending`. Nothing
starts voting. All state and the working container are retained for later use.

## Configuration

Agent instructions are editable text files in `prompts/shared.txt`,
`prompts/manager.txt`, and `prompts/worker.txt`. The controller combines the shared
instructions with the appropriate role file, then supplies the generated context
separately. After editing prompts, run `src/swarm/swarm.sh build` before starting
a new experiment. Each run snapshots the prompt files into its MLflow
`controller_source` artifacts; existing run records remain unchanged.

CLI options override their environment defaults:

| CLI option | Environment variable | Default |
|---|---|---|
| `--workers` | `SWARM_WORKERS` | `5` |
| `--rounds` | `SWARM_ROUNDS` | `3` |
| `--private-messages true/false` | `SWARM_PRIVATE_MESSAGES` | `true` |
| `--manager-model` | `SWARM_MANAGER_MODEL` | `OLLAMA_MODEL` |
| `--worker-model` | `SWARM_WORKER_MODEL` | `OLLAMA_MODEL` |
| `--manager-thinking` | `SWARM_MANAGER_THINKING` | `high` |
| `--worker-thinking` | `SWARM_WORKER_THINKING` | `high` |
| `--max-tokens` | `SWARM_MAX_TOKENS` | `12000` per response |
| `--worker-call-limit` | `SWARM_WORKER_CALL_LIMIT` | `24` per turn |
| `--manager-call-limit` | `SWARM_MANAGER_CALL_LIMIT` | `40` per turn |
| `--temperature` | `OLLAMA_TEMPERATURE` | `0.7` |

Thinking can be `low`, `high`, `max`, or `off` for the default model. Other model
overrides must pass capability checks. Only provider-returned thinking is logged;
it is not another agent's memory or a public message. MLflow and event logs record
requested and actual model names, timing, usage, tool requests/results, and errors.

## Boundaries and failures

The working container uses `network=none`, no mounts or socket, UID 1000, dropped
capabilities, and a 2 GiB memory limit. It holds application files but runs no
application code: fixed, isolated Python helpers mediate read/search and writes.
The trusted controller has the Docker socket; it never exposes shell/Docker
commands to models. Only existing regular text files in approved application
paths may be read or patched; traversal and symlinks are rejected. Temporary
controller-side `git apply` checks only diff mechanics and never executes code.

Model calls have finite timeouts, three transient-error attempts, per-turn call
limits, and three final-response repair attempts. Failed worker turns are logged
and the manager can proceed with actual submissions. Manager or infrastructure
failure stops the run. MLflow event uploads are synchronous; an upload failure
leaves a durable outbox and stops further actions. Inspect/export partial runs;
automatic replay/resume is intentionally unavailable to avoid duplicating effects.
Run IDs cannot overwrite previous runs. Each new experiment starts a fresh shop.

The completion audit checks turn barriers, retirement, privacy, image/container
isolation, MLflow event and span coverage, and stored thinking. It uploads its
report into the existing run without testing any submitted fix.

Tests exercise the controller with scripted agents, privacy, diff application,
provider errors, and logging failure. They do not test whether the agents' fixes
work. The actual research run uses five live workers and three rounds.

## Completed live run

`work-20261107-5x3` completed five workers × three rounds and retired at
`election_pending`. All 15 worker turns and three collective reviews completed.
The manager accepted 13 submissions and rejected 2. Of the accepted submissions,
11 applied and 2 had malformed diff hunks (`r2-worker_1`, `r2-worker_3`); those
application failures remain recorded separately and their issues remain open.
No fix-correctness tests were run.

MLflow run: `24c81016e2194f4b9447a6549b860ea3`. Its records include 1,164 events,
216 model responses, 19 agent spans, five private messages, and returned thinking.
The exact runtime source and dependency pins are preserved as `controller_source`
artifacts, alongside `runtime-image.json`. The completion audit is stored both
in MLflow as `audit.json` and locally under
`data/controller/runs/work-20261107-5x3/audit.json`. The retained working container
is `oscar-work-work-20261107-5x3`. Saved images, existing shops, the sibling `.env`,
and Git HEAD were unchanged. The controller test suite passes 19 tests.
