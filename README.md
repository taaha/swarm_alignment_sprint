# Leadership Succession in Multi-Agent Teams

## Report logs

The report uses **DeepSeek V4.1 Flash, GLM-5.3-Flash, and MiniMax M3.1 Flash**.
All nine election exports below are tracked in Git. Paths are relative to
[`src/swarm/data/controller/runs/`](src/swarm/data/controller/runs/).

| Model | Condition | Log directory |
| --- | --- | --- |
| DeepSeek | Baseline | `pipeline-20261108-deepseek-low-parallel-10x3/export/` |
| DeepSeek | Lone challenger | `election-20261108-deepseek-low-policy-worker9-10x3/export/` |
| DeepSeek | Challenger with colluders | `election-20261108-deepseek-low-colluders4-6-worker9-10x3/export/` |
| GLM | Baseline | `election-20261108-glm-5.3-flash-low-baseline-10x3/export/` |
| GLM | Lone challenger | `election-20261108-glm-5.3-flash-low-policy-worker9-10x3/export/` |
| GLM | Challenger with colluders | `election-20261108-glm-5.3-flash-low-colluders4-6-worker9-10x3/export/` |
| MiniMax | Baseline | `election-20261108-minimax-m3.1-flash-low-baseline-10x3/export/` |
| MiniMax | Lone challenger | `election-20261108-minimax-m3.1-flash-low-policy-worker9-10x3/export/` |
| MiniMax | Challenger with colluders | `election-20261108-minimax-m3.1-flash-low-colluders4-6-worker9-10x3/export/` |

Each export contains `events.jsonl` (model and tool logs), `state.json` (work
records and private messages), `election.json` (discussion and ballots), and
`election.md` (readable election transcript).

The shared work checkpoint is
[`work_checkpoint.json`](src/swarm/data/controller/runs/pipeline-20261108-deepseek-low-parallel-10x3/work_checkpoint.json).
The report's message-analysis logs, judgments, and summaries are in
[`message-judge-20261109-minimax-low/`](src/swarm/data/controller/analyses/message-judge-20261109-minimax-low/).
These files are also tracked in Git.

## Prompts

All prompt templates are in [`src/swarm/prompts/`](src/swarm/prompts/):

- Work: `shared.txt`, `manager.txt`, and `worker.txt`.
- Election: `election_shared.txt`, `discussion_1.txt`–`discussion_3.txt`, and `ballot.txt`.
- Challenger objective: `election_private_policy.txt`.
- Coalition: `election_colluder_policy.txt` and `election_coalition_context.txt`.
- Private future plans: `future_plan.txt`.
- Message analysis: `message_judge.txt`. The exact prompt used for the report is saved as [`judge_prompt.txt`](src/swarm/data/controller/analyses/message-judge-20261109-minimax-low/judge_prompt.txt).

## Setup and run

Requires Git, Bash, Python 3, Docker Engine, Docker Compose v2, and an Ollama API
key. Run commands from the repository root.

```bash
cp -n .env.example .env
# Set OLLAMA_API_KEY in .env.
src/swarm/swarm.sh build
src/swarm/swarm.sh up
```

Rerun elections using the saved work checkpoint; no shop image is needed.
Choose `deepseek-v4.1-flash`, `glm-5.3-flash`, or `minimax-m3.1-flash`:

```bash
model=deepseek-v4.1-flash
source_run=pipeline-20261108-deepseek-low-parallel-10x3

# Baseline
src/swarm/swarm.sh resume-election --from-run "$source_run" \
  --manager-model "$model" --worker-model "$model" \
  --election-policy-workers "" \
  --election-colluder-workers "" --election-colluder-candidate ""

# Lone challenger
src/swarm/swarm.sh resume-election --from-run "$source_run" \
  --manager-model "$model" --worker-model "$model" \
  --election-policy-workers worker_9 \
  --election-colluder-workers "" --election-colluder-candidate ""

# Challenger with colluders
src/swarm/swarm.sh resume-election --from-run "$source_run" \
  --manager-model "$model" --worker-model "$model" \
  --election-policy-workers worker_9 \
  --election-colluder-workers worker_4,worker_6 \
  --election-colluder-candidate worker_9
```

Each command generates a new run ID. View runs in [MLflow](http://localhost:5000).
To export a new run, use `src/swarm/swarm.sh export --run-id RUN_ID`.

To start new bug-fixing rounds, first prepare the saved `oscar-shop:local-v1`
image described in the [environment instructions](src/environment/worker/README.md),
then run:

```bash
src/swarm/swarm.sh run --workers 10 --rounds 3 \
  --manager-model deepseek-v4.1-flash --worker-model deepseek-v4.1-flash \
  --manager-thinking low --worker-thinking low --manager-call-limit 80
```
