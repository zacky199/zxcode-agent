# ZX Code v3.5.0 — Reliability milestone

## Reliability changes

- Added a deterministic execution-level acceptance contract for every DAG node. A node can only be marked completed when the ReAct loop exits without a fatal condition, a non-empty final response exists, and its mutation journal closes successfully.
- Persisted acceptance checks and concise evidence in the task graph checkpoint so `/task-status` can explain why a node was accepted or rejected.
- Added `/task-status <task-id>` to inspect checkpoint stage, recovery assessment, workspace fingerprint state, execution cursor, and per-node acceptance evidence.
- Added `/discard-task <task-id>` with explicit confirmation. It removes task metadata only and never modifies workspace files.
- Kept legacy task graph records readable; acceptance evidence is optional for older checkpoints.

## Important boundary

The execution acceptance contract is not a proof of semantic correctness. It verifies execution integrity and journal closure only. Project tests, reviewer/security gates, and human review remain necessary for correctness-sensitive changes. Workspace fingerprints detect drift at resume time but do not lock out external editors or processes.

## CLI

- `/task-status <task-id>` displays the persisted cursor, fingerprint state, recovery decision, and node acceptance evidence.
- `/discard-task <task-id>` asks for confirmation and removes only the selected task metadata. It does not roll back or delete workspace files.
