# ZX Code v3.5.1 reliability plan

## Delivered
1. Crash-injection tests at task checkpoint, invocation claim, journal pending-operation, and post-mutation/pre-close boundaries.
2. Process-shared POSIX workspace lock covering the full `chat_stream` lifetime.
3. Optional node-specific verification plans, run with explicit authorization in a read-only container workspace mount, with bounded stdout/stderr and persisted exit-code evidence.
4. Regression coverage for lock contention, abrupt process death, verification acceptance, and checkpoint persistence.

## Remaining work
- Expand crash injection to every journal and checkpoint fsync/rename boundary using deterministic fault hooks.
- Validate locking semantics on supported network filesystems or explicitly prohibit them.
- Add lock ownership diagnostics to `/task-status`.
- Add verification command risk classification and project-specific policy profiles.
- Add an end-to-end integration test using a real container runtime and a deliberately failing verification command.
