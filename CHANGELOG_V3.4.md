# ZX Code v3.4 — Changelog

## v3.4.0-dev4
- Added deterministic node-level DAG scheduling, node checkpoints, safe-boundary resume via `/resume-task`, and mutation tool-call idempotency ledger.
- Running/interrupted nodes are never replayed automatically.
- See `CHANGELOG_V3.4-dev4.md` and `TEST_REPORT_V3.4-dev4.md`.

## v3.4.0-dev3
- Added phase-level execution cursor and conservative crash-recovery assessment.
- Task-state v2 writes with v1 read compatibility.
- `/tasks` reports advisory recovery action; no unsafe automatic replay.
- See `CHANGELOG_V3.4-dev3.md` and `TEST_REPORT_V3.4-dev3.md`.


## DAG validation and dependency-aware execution
- Added strict TaskGraph validation for duplicate IDs/dependencies, unknown/self dependencies, invalid states, and dependency cycles.
- Added deterministic topological ordering, ready-node discovery, and blocked-descendant propagation primitives.
- Planner now resolves forward dependency references instead of silently dropping them; malformed/cyclic plans fall back to one explicit original-request step.
- Agent now executes its plan prompt in dependency-safe topological order and explicitly prohibits parallel workspace mutations.
- Serialized task graph is preserved through Coder, Tester, Reviewer, Security Auditor, and terminal task states.
- Task IDs now use UUIDs rather than millisecond timestamps to avoid collisions between fast/concurrent task starts.
- Stored metadata status is labeled `stored`, not `recoverable`, to avoid implying that automatic resume already exists.
- Added tests for DAG ordering, forward dependencies, cycle rejection, invalid dependencies, blocked descendants, graph persistence, and end-to-end agent prompt ordering.

## v3.4.0-dev1 retained changes
- Evidence-based bounded self-repair with terminal-output untrusted-data handling and common credential redaction.
- Atomic per-task metadata records, active pointer, allow-listed payload persistence, task listing, and `/tasks` CLI command.
- Gap analysis, technical debt register, and v3.4 implementation plan.

## Still not implemented
- Automatic resume controller and execution cursor recovery.
- A scheduler that dispatches each DAG node as an isolated execution unit; the current agent receives a dependency-safe sequential plan.
- Parallel execution of independent nodes. This remains intentionally disabled until conflict isolation and idempotency are implemented.
