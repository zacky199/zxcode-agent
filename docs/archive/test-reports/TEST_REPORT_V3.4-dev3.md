# ZX Code v3.4.0-dev3 — Verification report

## Automated tests

- `python -m compileall -q .`: PASS.
- Full regression suite: **323 passed**, 11 expected `RuntimeWarning` messages from tests explicitly opting into unsafe host execution without a sandbox.
- Added tests cover cursor round-trip/sequence, invalid cursor rejection, terminal-task handling, interrupted mutation requiring manual reconciliation, pre-execution restart requiring confirmation, task-store cursor persistence, and v1 state compatibility.

## Recovery semantics

| Persisted state | Assessment | Automatic execution |
|---|---|---|
| completed / blocked / failed | inspect_result | No |
| executing / verification / review | manual_reconciliation | No |
| planning / planned, no mutation recorded | restart_from_user_confirmation | No |

The cursor is a durable phase-level record, not a node-level execution journal. The current ReAct loop can execute multiple tool calls between phase checkpoints. Therefore dev3 intentionally does not claim crash-safe automatic resume.

## Compatibility

- New writes use task-state schema v2.
- Reads accept schema v1 and v2.
- Provider credentials, environment variables, provider objects, and full conversation/tool transcripts are not added to task-state persistence.

## Not yet implemented

- Node-by-node DAG scheduler and per-node execution checkpoints.
- Automatic resume controller / `/resume-task` command.
- Safe replay/idempotency protocol for mutating tools.
- Workspace fingerprint and source-drift reconciliation.
- Parallel mutation isolation.
