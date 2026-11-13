# ZX Code v3.4.0-dev4 — Verification report

## Automated tests

- `python -m compileall -q .`: PASS.
- Full regression suite: **328 passed**, 11 expected `RuntimeWarning` messages from tests explicitly opting into unsafe host execution without an OS sandbox.
- New tests cover node-by-node dependency execution, prerequisite evidence propagation, resume skipping completed nodes, refusal to replay a running node, mutation call-ID idempotency, argument mismatch rejection, and graph state persistence.

## Scheduler behavior

- Ready nodes are selected deterministically using the validated DAG.
- Only one DAG node is dispatched at a time; delegated sub-agents inside a node are also run sequentially.
- A node is checkpointed as `running` before its ReAct loop starts.
- A node becomes `completed` only after the loop exits without a fatal error; its result is persisted before the next node starts.
- A failed node is marked `failed`; pending descendants are marked `blocked`.

## Recovery behavior

| Persisted state | Resume action |
|---|---|
| Node completed, next node pending, no in-flight mutation | `/resume-task <task-id>` continues pending nodes |
| Node running at interruption | Refuse automatic replay; manual reconciliation required |
| Task terminal (`completed`, `blocked`, `failed`) | Inspection only |
| Verification/review interrupted | Manual reconciliation |

The explicit `/resume-task` command is the user's confirmation to continue from a safe boundary. It is not authorization to replay a node that was running at interruption.

## Mutation idempotency

- The journal writes an invocation claim before a mutating tool is dispatched.
- A duplicate call ID with matching arguments is suppressed.
- Reusing a call ID with different arguments is rejected.
- An `in_flight` record is treated as uncertain; it is not re-executed.

## Limitations

- Exactly-once semantics cannot be guaranteed for arbitrary shell commands, remote APIs, plugins, or other external side effects.
- Node-level task results are bounded text evidence, not a complete transcript or replay log.
- No parallel mutation execution or cross-process workspace locking was added.
- An interrupted running node must be reconciled manually, even if it might not have mutated anything.
