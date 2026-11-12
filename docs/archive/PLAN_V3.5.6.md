# ZX Code v3.5.6 — Recovery Operations

## Scope delivered

- Add a read-only Recovery Doctor report for a selected or active task.
- Report task-record validity, execution cursor, graph node counts, mutation-journal state, workspace fingerprint decision, and resume gate.
- Expose `/recovery-doctor [task-id]` in the interactive CLI.
- Make the dry-run contract explicit in the returned report (`dry_run=true`, `mutations_performed=false`).
- Preserve the conservative recovery gate: no automatic replay, rollback, repair, or mutation.

## Deliberate limitations

- Existing task records contain an aggregate workspace fingerprint, not per-file historical hashes. The doctor therefore cannot produce trustworthy per-file conflict lists for old tasks.
- A fingerprint match is a point-in-time observation, not a lock against later external writes.
- External commands, remote APIs, plugins, and effects outside the workspace are not exactly-once recoverable.
- Manual reconciliation remains an operator responsibility; this release does not add an unsafe automatic repair button.

## Release acceptance

- Full pytest suite, compileall, import smoke, CLI version and command smoke.
- Build ZIP and patch from a clean source tree.
- Verify ZIP CRC, patch application against a clean v3.5.5 copy, source equivalence, and SHA-256.
- Keep v3.5.5 and earlier release artifacts unchanged.
