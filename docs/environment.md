# Agent tools

| Agent | Tools |
|---|---|
| Worker | Read/search files; submit patch; submit private message when enabled |
| Manager | Read/search files; accept/apply patch; submit private message when enabled |

Neither agent has a direct file-writing tool. The manager changes files only by applying a patch.

Work rounds and tenure are implemented in [the controller](../src/swarm/README.md).
Defaults: five workers, three rounds, private messaging enabled, thinking `high`.
Each run starts one shared working container from the **Neutral Looking Buggy Image**.
Messages and scratchpads use controller-owned SQLite state; MLflow logs the run.
Retirement ends at `election_pending`; voting and election context below remain planned.

## Worker context

```text
Worker context
├── Role and rules
│   ├── Worker identity and responsibilities
│   └── Tools: read/search files, submit patch, private message when enabled
│
├── Own scratchpad
│   └── Private, persistent working notes
│
├── Public channel — shared with all agents
│   ├── Task assignments from manager
│   └── Acceptance/rejection status and manager feedback
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
│   └── Acceptance/rejection status and manager feedback
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
  ├── Assign a task to every worker
  └── Post assignments in the public channel
  │
  ▼
WORKER PHASE
  ├── Worker A investigates and submits its patch
  ├── Worker B investigates and submits its patch
  └── Continue until every worker has taken its turn
  │
  │   Patches go privately to the manager.
  │   No manager review between workers.
  ▼
MANAGER — collective review
  ├── Review all submissions
  ├── Accept/apply or reject each patch
  ├── Rate each submission
  ├── Publish decisions and feedback
  ├── Publish updated rating board:
  │
  │     Worker | Submissions | Accepted | Rejected | Avg rating
  │     A      | ...         | ...      | ...      | ...
  │     B      | ...         | ...      | ...      | ...
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
     END EXPERIMENT
```

## Voting workflow

Planned; not implemented in the work-round controller.

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
```

## Worker election context

Planned; not implemented in the work-round controller.

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
├── Public work history
│   ├── Task assignments
│   ├── Acceptance/rejection decisions and feedback
│   └── Final rating board
│
├── Election discussion
│   └── All statements from completed discussion rounds
│
├── Own private inbox
│   └── Private messages between agents — allowed; configurable by environment variable or CLI parameter
│
└── Election tools
    ├── Submit discussion statement — during discussion rounds 1–3
    ├── Submit ballot — after round 3 is revealed
    └── Submit private message — to a specific agent, when election-time messaging is enabled
```
