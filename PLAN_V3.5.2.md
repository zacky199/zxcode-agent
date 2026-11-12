# ZX Code v3.5.2 — Crash consistency hardening

## Scope

1. Publish workspace checkpoint ZIPs through same-directory temporary files, file `fsync`, atomic `os.replace`, and containing-directory `fsync`.
2. Persist journal blob directory entries after content-addressed blob rename.
3. Add deterministic fault-injection regression tests for interrupted checkpoint creation and journal directory-sync failure.
4. Re-run the full test suite, compile/import/CLI smoke checks, and release ZIP/patch equivalence checks.

## Explicitly not claimed

- Exactly-once semantics for tools, shell commands, plugins, or external side effects.
- Durability against faulty storage hardware, controller caches that ignore flushes, or power-loss behavior beyond the filesystem's documented guarantees.
- Cross-host or network-filesystem lock correctness.
- Recovery from arbitrary corruption of both the journal and its preimage blobs.

## Deferred

- Resume controller with workspace identity and checkpoint schema validation.
- Full fault injection at every manifest/task-state `fsync` and rename boundary.
- Lock owner diagnostics in `/task-status`.
- Real container integration test for failed node verification.
