# ZX Code v3.5.1 — Reliability hardening

## Crash-injection coverage

- Added subprocess crash tests that terminate immediately after a task checkpoint save, after a durable tool-invocation claim, after a mutation journal records a pending operation, and after a workspace mutation has happened but before the journal can close.
- Verified the checkpoint and journal manifests remain readable after abrupt process exit.
- Verified a checkpoint with a running node and `mutation_started=true` remains classified as `manual_reconciliation`; the agent does not automatically replay that node.

## Workspace concurrency protection

- Added a process-shared POSIX `flock` lock at `.zx_checkpoints/workspace.lock`.
- `chat_stream` acquires the lock before task work and releases it when the stream ends. Process termination releases the kernel lock automatically.
- Concurrent ZX Code tasks targeting the same workspace fail closed with a clear busy error.
- Lock metadata records PID, hostname, and start time. The lock file is intentionally persistent; lock ownership is the kernel file lock, not stale PID text.

## Per-node verification plans

- Planner nodes may declare up to five `verification_commands`, each bounded to 500 characters.
- Commands execute through the configured Tester sandbox with a 120-second per-command timeout and explicit command authorization.
- Verification uses a read-only workspace mount and is blocked when the sandbox is configured for host execution.
- Exit code, bounded stdout/stderr, command, and status are persisted as per-node verification evidence in task checkpoints.
- A declared verification plan must pass before a DAG node can be accepted. Nodes without a plan are explicitly marked `not_configured`; they are not represented as verified.

## Compatibility

- New task graph fields are optional when loading older checkpoints.
- Task state allow-list persists bounded verification metadata only; no provider credentials or raw provider objects are added.

## Limitations and risks

- `flock` is advisory and coordinates only ZX Code processes using this lock path. It cannot stop edits by IDEs, shells, build daemons, other programs, or processes on systems that do not honor the same filesystem lock semantics.
- Locking currently requires POSIX. Windows execution fails closed rather than silently running without a lock.
- Read-only verification requires the container sandbox and configured approval callback. Host-mode verification is blocked. Container commands may still write to container temporary storage, but not to the mounted workspace.
- Verification plans are optional. Absence of a plan is `not_configured`, not a pass.
- Crash tests cover selected durability boundaries, not every possible instruction-level crash point, storage-controller failure, filesystem corruption, or power-loss scenario.
- This release does not claim exactly-once semantics for shell commands, remote APIs, plugins, or external side effects.
