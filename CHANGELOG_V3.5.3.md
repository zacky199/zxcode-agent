# ZX Code v3.5.3 — Resume & Recovery Engine

## Added / changed

- Centralized resume eligibility now validates task graph structure and cursor references.
- Recovery assessment checks workspace fingerprint before returning a resumable decision.
- Recovery assessment inspects journals belonging to the task's nodes; malformed or unreadable journals, unfinished operations, non-completed tool invocation records, and rollback skips block continuation.
- Resume now acquires the workspace advisory lock before checkpoint loading and recovery assessment, retaining it through resumed execution startup to close the internal ZX Code time-of-check/time-of-use window.
- An `executing` cursor is always treated as requiring manual reconciliation, even if its mutation flag is inconsistent.

## Safety / limitations

- Recovery remains conservative and does not perform automatic replay or automatic rollback.
- External processes can still modify the workspace without honoring the advisory lock; fingerprinting narrows but cannot eliminate that race.
- No exactly-once semantics are claimed for shell commands, plugins, APIs, or other external effects.
- Container E2E and physical power-loss tests were not run.
