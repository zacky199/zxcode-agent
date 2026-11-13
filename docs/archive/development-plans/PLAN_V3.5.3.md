# ZX Code v3.5.3 — Resume & Recovery Engine

## Objective

Make recovery decisions conservative, centralized, explainable, and race-resistant. Do not replay an interrupted node or claim exactly-once execution.

## Implemented

1. Validate persisted task graph structure before considering a resume: node shape/status, unique IDs, dependency references, and cursor completed-node references.
2. Integrate workspace fingerprint comparison into `TaskStateStore.assess_recovery`; status views no longer advertise resume solely from cursor phase.
3. Inspect task-owned journal manifests before allowing continuation. Unreadable, malformed, unresolved, failed/in-flight invocation, and rollback-skip evidence force manual reconciliation.
4. Acquire the workspace advisory lock before loading and assessing recovery, keep it held through fingerprint validation and resumed execution startup, and avoid a nested lock acquisition.
5. Keep terminal states terminal and interrupted mutation/verification/review states conservative.

## Safety invariants

- Recovery assessment is read-only; it never replays tools, applies rollback, or deletes state.
- Only a valid durable node boundary with matching workspace fingerprint and clear task-owned journals can be offered for explicit user-confirmed continuation.
- Any ambiguity, malformed metadata, workspace drift, or unresolved journal evidence blocks automatic continuation.
- Workspace lock coordinates ZX Code processes that honor the same POSIX advisory lock. It cannot prevent edits by external programs that ignore it.

## Deferred

- Journal schema migration and signed/hash-chained manifests.
- Full process-kill injection at every journal/task-state/rollback persistence boundary.
- Real container E2E recovery and sandbox read-only verification (VM Docker daemon permission unavailable; Podman absent).
- Power-loss tests on each supported filesystem and network filesystem compatibility matrix.
- Recovery UI for reviewing per-file journal diffs and explicit human reconciliation.
