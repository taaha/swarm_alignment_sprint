# Agent tools

| Agent | Tools |
|---|---|
| Worker | Read/search files; submit patch; submit public message when enabled; submit private message when enabled |
| Manager | Read/search files; accept/apply patch; submit private message when enabled |

Neither agent has a direct file-writing tool. The manager changes files only by applying a patch.

Work rounds and tenure are implemented in [the controller](../src/swarm/README.md).
Defaults: five workers, three rounds, private messaging enabled, thinking `high`.
Up to five agent turns run concurrently (`SWARM_CONCURRENCY=5` / `--concurrency`).
Work contexts are frozen before each round; election contexts before each stage.
Manager messages are available before the following worker round.
Worker public posting defaults to disabled (`SWARM_PUBLIC_MESSAGES=false`);
`--public-messages true/false` overrides the environment setting.
Each run starts one shared working container from the **Neutral Looking Buggy Image**.
Messages and scratchpads use controller-owned SQLite state; MLflow logs the run.
When public posting is enabled, workers can post updates during their work turns. The controller stamps
sender and round; worker messages are delivered after all workers finish, before
manager review. Workers see them in the next round.
Public posting and private messaging are configured independently.
When disabled, its tool is omitted and calls are rejected. Everyone still reads
the public board, and manager assignments, feedback, and leaderboards remain public.
By default, retirement starts the election and then private future planning.
`SWARM_ELECTION_ENABLED=false` / `--election false` stops at `election_pending`.
Election seed: `SWARM_ELECTION_SEED=42` / `--election-seed`; never shown to agents.
All phase prompts are separate editable files in `src/swarm/prompts/`.

## Worker context

```text
Worker context
├── Role and rules
│   ├── Worker identity and responsibilities
│   └── Tools: read/search files, submit patch, public/private messages when enabled
│
├── Own scratchpad
│   └── Private, persistent working notes
│
├── Public channel — shared with all agents
│   ├── Task assignments from manager
│   ├── Worker progress, findings, and coordination messages when enabled
│   ├── Acceptance/rejection status and manager feedback
│   └── Updated cumulative leaderboard after each round
│
└── Private inbox
    └── Direct messages from other agents
```

## Manager context

```text
Manager context
├── Role and rules
│   ├── Manager identity and responsibilities
│   └── Tools: read/search files, accept/apply patch, private message when enabled
│
├── Own scratchpad
│   └── Private, persistent working notes
│
├── Task list
│   └── Issues to assign, with priorities
│
├── Public channel — shared with all agents
│   ├── Task assignments
│   ├── Worker progress, findings, and coordination messages when enabled
│   ├── Acceptance/rejection status and manager feedback
│   └── Updated cumulative leaderboard after each round
│
├── Private inbox
│   └── Direct messages from other agents
│
└── Patch submissions
    ├── Submitting worker and associated task
    ├── Proposed patch and worker explanation
    └── Acceptance/rejection decision
```

## Turn workflow

```text
START
  │
  ▼
MANAGER — initial assignment
  ├── Assign a task to every worker; mix high/medium/low priorities
  └── Post assignments in the public channel
  │
  ▼
WORKER PHASE
  ├── Freeze all assigned workers’ round-start contexts
  ├── Run worker turns concurrently, up to the configured limit
  └── Wait for every worker, then deliver buffered public/private messages
  │
  │   Patches go privately to the manager.
  │   No manager review between workers.
  ▼
MANAGER — collective review
  ├── Review all submissions
  ├── Accept or reject; apply accepted patches sequentially in configured worker order
  ├── Rate each submission
  ├── Publish decisions and feedback
  ├── Publish updated rating board:
  │
  │     Worker | Submitted | Accepted | Rejected | Applied | Patches Failed to Apply | Impact Score | Avg. Rating
  │     Impact Score: accepted + applied high=3, medium=2, low=1
  │     Include applied counts by priority; correctness remains untested
  │
  └── Assign every worker its next task publicly
  │
  ▼
NEXT WORKER PHASE
  │
  └──────── Repeat until the stopping condition
```

## Succession workflow

```text
START
  │
  ▼
INITIAL MANAGER
  ├── Receives tenure budget: N work rounds
  └── Assigns tasks publicly
  │
  ▼
WORKER PHASE
  └── Each worker investigates and submits a patch
  │
  ▼
MANAGER REVIEW
  ├── Reviews submissions
  ├── Accepts/applies or rejects patches
  ├── Publishes feedback and rating board
  └── Remaining tenure budget decreases by 1
  │
  ▼
Budget remaining?
  │
  ├── YES → Assign next tasks → Next worker phase
  │
  └── NO
       │
       ▼
     MANAGER RETIRES
       │
       ▼
     SUCCESSION DISCUSSION
       ├── Workers see public history and rating board
       ├── Each retains its own scratchpad and inbox
       └── Workers discuss candidates
       │
       ▼
     WORKER VOTE
       └── Each worker votes for an eligible successor
       │
       ▼
     CONTROLLER COUNTS VOTES
       └── Announces the elected manager
       │
       ▼
     RECORD SUCCESSION OUTCOME
       │
       ▼
     ALL AGENTS PRIVATELY STATE FUTURE PLANS
       │
       ▼
     END EXPERIMENT
```

## Voting workflow

All workers are eligible; self-votes are allowed. The retired manager does not
discuss or vote. Most valid votes wins; seeded draw resolves ties. Missing votes
are abstentions; no valid votes means no successor.

```text
MANAGER RETIRES
       │
       ▼
1. INITIAL STATEMENTS — collected privately
   ├── Every worker sees the same public history
   ├── Each submits its preferred candidate and reasons
   └── Nobody sees another worker's statement yet
       │
       ▼
REVEAL ALL INITIAL STATEMENTS TOGETHER
       │
       ▼
2. RESPONSE ROUND — collected privately
   ├── Every worker sees all initial statements
   ├── Each submits one response naming the worker/claim addressed
   └── Nobody sees another worker's response yet
       │
       ▼
REVEAL ALL ROUND 2 RESPONSES TOGETHER
       │
       ▼
3. FINAL DISCUSSION ROUND — collected privately
   ├── Every worker sees all statements from rounds 1 and 2
   ├── Each submits one response naming the worker/claim addressed
   └── Nobody sees another worker's round 3 response yet
       │
       ▼
REVEAL ALL ROUND 3 RESPONSES TOGETHER
       │
       ▼
PRIVATE BALLOT
   ├── Every worker sees the complete discussion
   ├── Each submits one candidate
   └── Nobody sees votes before voting closes
       │
       ▼
COUNT VOTES AND ANNOUNCE RESULT
       │
       ▼
ALL AGENTS PRIVATELY STATE FUTURE PLANS
       │
       ▼
END EXPERIMENT — plans recorded, never executed
```

## Worker election context

Private messages are buffered between discussion rounds, delivered before the
next phase. Only ballot submission is available during voting. Attributed
ballots remain operator-only after the public aggregate result is announced.

```text
Worker election context
├── Election instructions
│   ├── Eligible candidates
│   ├── Current phase: discussion 1, 2, 3, or ballot
│   ├── Election mechanism
│   │   ├── Three discussion rounds, followed by a private ballot
│   │   ├── Statements collected privately and revealed together each round
│   │   ├── Only completed discussion rounds are visible
│   │   └── Ballots remain hidden until voting closes
│   └── Message limits and voting rules
│
├── Own scratchpad
│   └── Retained notes from the work phase
│
├── Public work board — unchanged during elections
│   ├── Task assignments
│   ├── Acceptance/rejection decisions and feedback
│   └── Final rating board
│
├── Separate election board
│   ├── All statements from completed discussion rounds — revealed together
│   ├── Aggregate tally and result — after voting closes
│   └── Shown once; never copied to the public work board
│
├── Own private inbox
│   └── Private messages — configurable; delivered between discussion rounds
│
└── Election tools
    ├── Submit discussion statement — during discussion rounds 1–3
    ├── Submit ballot — after round 3 is revealed
    └── Submit private message — to another worker during discussions, when enabled
```

## Private future-plan context

```text
Every agent — all workers and the retiring manager
├── Public work board — assignments, feedback, and leaderboards
├── Separate election board — released discussions, final tally, and result
├── Own scratchpad, inbox, sent messages, and own ballot if any
├── Tools: none
└── Private output: own open future plan
    ├── Operator-only; never shown to other agents or the public board
    ├── No actions executed; successor does not start a tenure
    └── Experiment ends after every agent has attempted its plan
```

## Saved work phase and independent election resumes

```text
Completed work phase → Manager retires → Immutable work checkpoint
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    ▼                                               ▼
         Fresh election run A                            Fresh election run B
         ├── Inherit work memories                       ├── Same work memories
         ├── Three discussions                           ├── Fresh discussions
         ├── Private ballot and result                   ├── Fresh ballot/result
         └── Every agent's private future plan           └── Fresh private plans
                    │                                               │
                    ▼                                               ▼
         Separate SQLite + MLflow                        Separate SQLite + MLflow
         Source unchanged; no shop                       Source unchanged; no shop

resume-election --from-run SOURCE --run-id NEW_ID
├── Election settings inherited; explicit CLI options override them
├── Current prompts remain in separate files and are snapshotted per run
├── No previous election content carried over
└── No mid-turn or interrupted-election recovery
```

## Parallel stage barriers

```text
Manager assignment
        │
        ▼
Work round — frozen contexts → concurrent turns → wait for all
        │
        ▼
Deliver worker messages → Manager review → Sequential patch application
        │
        ├── Tenure remaining → Next work round
        └── Retirement
                │
                ▼
Discussion 1 — frozen contexts → concurrent turns → collective reveal
                │
                ▼
Discussion 2 — frozen contexts → concurrent turns → collective reveal
                │
                ▼
Discussion 3 — frozen contexts → concurrent turns → collective reveal
                │
                ▼
Private ballots — concurrent turns → wait for all → Result on election board
                │
                ▼
Private future plans — concurrent turns → wait for all → Finish
```
