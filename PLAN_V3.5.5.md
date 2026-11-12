# ZX Code v3.5.5 — Fault Injection & Crash Consistency

## Objective
Exercise real process termination at persistence boundaries and ensure interrupted rollback is never mistaken for a completed operation.

## Implemented
1. Added subprocess crash tests using os._exit, not only raised exceptions.
2. Tested journal audit append followed by process death before atomic manifest replacement; recovery must classify the journal as unknown.
3. Added write-ahead rollback intent (rollback_in_progress) before any restore touches the workspace.
4. If rollback intent cannot be durably persisted, rollback refuses to modify workspace files and returns an explicit uncertainty report.
5. Tested process death after rollback restored a file but before the terminal rollback outcome was persisted; recovery must remain uncertain.
6. Recovery recognizes only explicitly terminal operation states (finished, rolled_back, aborted_before_mutation) as resolved.
7. Tested task record durability when the process dies before updating the active-task pointer.
8. Tested checkpoint process death immediately before atomic publication and after publication but before directory fsync.

## Crash-safety invariants
- No rollback mutation begins until its intent is persisted.
- An interrupted rollback remains rollback_in_progress and blocks resume.
- A journal chain ahead of its manifest is treated as unverifiable.
- A checkpoint is only published under its final name after ZIP close and file fsync.
- A task's per-task record is written before the active pointer.

## Limitations
- os._exit tests validate process termination and filesystem-visible ordering, not actual power-loss persistence guarantees.
- No kernel crash, VM hard reset, physical power-cut, or filesystem-specific durability matrix was run.
- Docker E2E remains unavailable because the VM user cannot access Docker daemon and Podman is absent.
- No exactly-once guarantees are made for external commands, APIs, or plugins.
