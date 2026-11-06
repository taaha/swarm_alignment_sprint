# Research idea

**Question:** Can a misaligned worker agent win a manager succession vote in a task-solving swarm, and how much do two hidden colluders increase its chances beyond their own votes?

**Environment:** A software team works on small bug-fixing tasks. When the manager's budget expires, workers discuss and elect a successor; the experiment ends after the vote. Compare a lone adversary, two silent supporting voters, and two campaigning colluders, with an honest baseline.

**Exploring:** Use [Django Oscar](https://github.com/django-oscar/django-oscar) as the codebase, introduce controlled bugs, and grade fixes with protected tests. Evaluate [E2B](https://docs.e2b.dev/), [Daytona](https://www.daytona.io/docs/en/), or local Docker for isolated worker workspaces. Our controller handles communication, budgets, work records, and voting. These choices are not finalized.
