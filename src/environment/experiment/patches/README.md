# Introduced bug patches

This directory contains all 100 approved mutations, numbered to match the root
catalog and checklist. Each patch is a sequential delta against the state after
the preceding patch; replay in numeric order. Several patches touch the same
file or method.

Each patch was applied in the ignored experiment workspace, observed failing its
behavioral oracle, temporarily reverted to verify repair, and then reapplied.
Hashes and per-bug evidence are recorded in `../grading/verification.json`.

The local `swarm-oscar:buggy-v1` image contains all 100 mutations. The saved
baseline and ready images remain unchanged. These patches and the grading
instructions are deliberately excluded from worker images.
