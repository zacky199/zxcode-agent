# ZX Code v3.4.0-dev3

## Crash recovery foundation
- Added `ExecutionCursor` with validated phases, monotonic checkpoint sequence, completed node IDs, current node ID, and mutation-in-flight marker.
- Agent persists cursor checkpoints at planning, execution start, coder completion, verification, review, and terminal boundaries.
- Task state schema advances to v2 while retaining read compatibility for v1 records.
- Added conservative recovery assessment: terminal tasks are inspection-only; interrupted execution/verification/review requires manual reconciliation; pre-execution interruptions require user confirmation before restart.
- `/tasks` now displays the advisory recovery action for each stored task.
- No provider credentials, environment variables, provider objects, or raw conversation/tool transcripts are persisted by the cursor.

## Safety boundary
This release does **not** implement automatic task continuation or node-level DAG scheduling. The current ReAct loop can mutate files between durable phase checkpoints. A crash during that interval is therefore classified as `manual_reconciliation`; the agent must not blindly replay the request or tool calls.

## Verification
- Added cursor serialization, invalid-state, terminal-state, interrupted-mutation, legacy-v1 compatibility, and task-store recovery tests.
- Full test results are recorded in `TEST_REPORT_V3.4-dev3.md`.
