# ZX Code v3.5.6

## Added
- Read-only Recovery Doctor diagnostics via `/recovery-doctor [task-id]`.
- Structured dry-run report for task state, execution cursor, graph, journal integrity state, workspace fingerprint and recovery gate.
- Explicit report flags proving that no recovery mutation was attempted.

## Safety and limitations
- Recovery remains fail-closed for uncertain mutation state; no automatic replay, rollback, or repair is performed.
- Per-file conflict attribution is not available for historical checkpoints because they persist only an aggregate workspace fingerprint.
- No exactly-once guarantee is made for external commands, APIs, plugins, or side effects.
