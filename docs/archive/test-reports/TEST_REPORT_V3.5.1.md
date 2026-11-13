# Test report — ZX Code v3.5.1

## Full regression suite

- Command: `.venv/bin/python -m pytest -q --disable-warnings --maxfail=1`
- Result: **354 passed, 11 warnings**.
- The 11 warnings are inherited expected unsafe-host-execution opt-in warnings from tests; no tests failed.

## Added reliability coverage

- Abrupt process exit after durable task checkpoint save.
- Abrupt process exit after durable tool invocation claim; `in_flight` state survives.
- Abrupt process exit after journal preimage/pending operation persistence.
- Abrupt process exit after a workspace mutation; recovery assessment requires manual reconciliation.
- Competing process lock contention, normal stream release, and kernel lock release after abrupt owner-process death.
- Per-node verification command execution, bounded output, exit-code evidence, and failed-verification acceptance rejection.
- Verification plan serialization and task-checkpoint persistence.
- Container verification mount is read-only; host-mode verification is rejected.

## Additional release checks

- Python compileall.
- Core module import smoke.
- CLI version smoke.
- ZIP CRC/integrity and extracted source smoke.
- Patch application against v3.5.0 and source-equivalence comparison.

## Not covered

- Exhaustive crash injection at every fsync/rename boundary.
- Power loss, faulty storage hardware, network filesystem lock semantics, and cross-host lock correctness.
- Protection against non-cooperating external editors/processes.
- Semantic quality of planner-generated verification commands; they remain approval-gated and should be reviewed.
- Exactly-once semantics for arbitrary external side effects.
