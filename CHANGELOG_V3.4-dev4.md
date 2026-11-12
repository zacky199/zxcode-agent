# ZX Code v3.4.0-dev4

## Node-level DAG scheduler
- Replaced the single whole-plan ReAct execution prompt with a deterministic sequential scheduler.
- Each ready DAG node is dispatched as an isolated ReAct execution unit.
- Nodes receive only their own task plus bounded evidence from completed prerequisites.
- Node state (`running`, `completed`, `failed`, `blocked`), result, and error metadata are checkpointed after each boundary.
- Failed nodes block dependent descendants; independent nodes remain pending and are not silently marked complete.
- Mutating nodes and delegated sub-agents remain sequential inside the DAG scheduler. No parallel mutation execution was introduced.

## Resume controller
- Added `ZXAgentPro.resume_task_stream()` and CLI command `/resume-task <task-id>`.
- Resume restores the validated serialized graph and skips completed nodes.
- Resume proceeds only from a safe node boundary where no node is recorded as running and no mutation is marked in-flight.
- Interrupted running nodes are refused and require manual workspace reconciliation. The agent does not replay them.
- `/tasks` displays the recovery assessment for each saved task.

## Mutation idempotency
- Mutation journal now persists a tool-call invocation ledger before execution and records the final status afterward.
- Duplicate provider tool-call IDs with identical tool and arguments are suppressed; reuse with different arguments is rejected.
- A call left `in_flight` after a crash is treated as uncertain and is never replayed.
- This is call-ID idempotency within a node journal, not a guarantee of exactly-once semantics for arbitrary external side effects.

## Safety boundaries
- Node execution is sequential; parallel execution remains disabled.
- A process crash during a running node requires manual reconciliation even if the operation may have been read-only. This is deliberately conservative.
- Shell commands and plugins can have external side effects beyond workspace files; the journal cannot reverse or prove exactly-once behavior for those systems.

## Verification
- See `TEST_REPORT_V3.4-dev4.md`.
