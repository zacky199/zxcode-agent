# Changelog

Seluruh riwayat perubahan ZX Code Agent, digabung dari berkas changelog per versi (versi terbaru di atas).

---

## V3.5.6

### ZX Code v3.5.6

## Added
- Read-only Recovery Doctor diagnostics via `/recovery-doctor [task-id]`.
- Structured dry-run report for task state, execution cursor, graph, journal integrity state, workspace fingerprint and recovery gate.
- Explicit report flags proving that no recovery mutation was attempted.

## Safety and limitations
- Recovery remains fail-closed for uncertain mutation state; no automatic replay, rollback, or repair is performed.
- Per-file conflict attribution is not available for historical checkpoints because they persist only an aggregate workspace fingerprint.
- No exactly-once guarantee is made for external commands, APIs, plugins, or side effects.

---

## V3.5.5

### ZX Code v3.5.5 — Fault Injection & Crash Consistency

- Added subprocess process-kill tests at journal, rollback, task-state and checkpoint publication boundaries.
- Added durable write-ahead rollback intent before restoring any workspace path.
- Rollback now refuses to modify workspace files if its intent cannot be persisted.
- Recovery now treats interrupted rollback as unresolved and only recognizes explicit terminal operation states.
- Verified task record remains available if process termination occurs before active-task pointer replacement.
- Verified checkpoint atomic publication behavior immediately before and after os.replace.
- Previous v3.5.4 release artifacts and source directory remain untouched.

---

## V3.5.4

### ZX Code v3.5.4 — Recovery Integrity

- Upgraded mutation journal manifests to schema v2 with canonical payload SHA-256 digests.
- Added a durable append-only SHA-256 audit chain for each journal owner.
- Recovery now verifies sequence continuity, previous-hash links, entry hashes, and manifest-to-chain-tail consistency before trusting journal state.
- Missing, legacy, truncated, malformed, or tampered journal evidence fails closed to manual reconciliation.
- Added regression coverage for valid chains, manifest tampering, chain truncation, and legacy journals.
- Previous v3.5.3 release artifacts and source directory remain untouched.

---

## V3.5.3

### ZX Code v3.5.3 — Resume & Recovery Engine

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

---

## V3.5.2

### ZX Code v3.5.2 — Crash consistency hardening

## Changed

- Checkpoint ZIPs are now built under a private temporary name in the checkpoint directory and published only after archive completion, file `fsync`, and atomic rename.
- The checkpoint directory is `fsync`ed after publication so the final directory entry is included in the durability sequence on supported POSIX filesystems.
- Journal preimage blobs now `fsync` the blob directory after content-addressed rename.
- Added deterministic regression tests for interrupted ZIP creation, checkpoint directory sync, journal blob directory sync, and directory-sync failure handling.

## Safety notes

- A failure after journal blob rename but before directory sync is surfaced as an error; the blob may exist, and callers must treat the operation as uncertain rather than retrying blindly.
- Temporary checkpoint files may remain after an uncatchable process kill; they are not trusted snapshots and are not published with the final `.zip` name.
- No exactly-once guarantee is made for external side effects.

---

## V3.5.1

### ZX Code v3.5.1 — Reliability hardening

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

---

## V3.5.0

### ZX Code v3.5.0 — Reliability milestone

## Reliability changes

- Added a deterministic execution-level acceptance contract for every DAG node. A node can only be marked completed when the ReAct loop exits without a fatal condition, a non-empty final response exists, and its mutation journal closes successfully.
- Persisted acceptance checks and concise evidence in the task graph checkpoint so `/task-status` can explain why a node was accepted or rejected.
- Added `/task-status <task-id>` to inspect checkpoint stage, recovery assessment, workspace fingerprint state, execution cursor, and per-node acceptance evidence.
- Added `/discard-task <task-id>` with explicit confirmation. It removes task metadata only and never modifies workspace files.
- Kept legacy task graph records readable; acceptance evidence is optional for older checkpoints.

## Important boundary

The execution acceptance contract is not a proof of semantic correctness. It verifies execution integrity and journal closure only. Project tests, reviewer/security gates, and human review remain necessary for correctness-sensitive changes. Workspace fingerprints detect drift at resume time but do not lock out external editors or processes.

## CLI

- `/task-status <task-id>` displays the persisted cursor, fingerprint state, recovery decision, and node acceptance evidence.
- `/discard-task <task-id>` asks for confirmation and removes only the selected task metadata. It does not roll back or delete workspace files.

---

## V3.4

### ZX Code v3.4 — Changelog

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

---

## V3.4-dev5

### ZX Code v3.4.0-dev5

## Reliability and recovery hardening

- Added bounded workspace fingerprints to task checkpoints (SHA-256 over file contents, paths, modes, directory structure, and symlink targets).
- Resume now compares the live workspace with the last durable checkpoint and refuses automatic continuation when drift is detected.
- Fingerprinting fails closed when traversal is incomplete, the workspace exceeds 20,000 files / 512 MiB, or a file changes while being read.
- Excludes VCS metadata, task checkpoint metadata, virtual environments, dependency directories, and common interpreter/test caches to avoid self-referential or volatile snapshots.
- ReAct iteration exhaustion and unresolved post-mutation verification now mark the active node as failed instead of allowing the DAG scheduler to mark it completed.
- Added regression coverage for fingerprint drift, checkpoint metadata exclusion, fingerprint limits, and end-to-end resume refusal after external workspace edits.

## Compatibility

- Task-state schema advanced to v3; v1 and v2 records remain readable.
- Older task records without a complete workspace fingerprint are not eligible for automatic resume and require manual reconciliation.
- Existing v3.4 DAG scheduling and mutation invocation ledger behavior is retained.

## Known limitations

- Fingerprints are bounded snapshots, not filesystem locks. A concurrent external writer can still change files after the comparison and before execution; run ZX Code in a workspace not being edited concurrently.
- Ignored directories such as `.git`, `.venv`, `node_modules`, and caches are not fingerprinted. Source manifests and lockfiles remain fingerprinted.
- A node may still produce a misleading textual report despite non-empty output. Independent Tester / Reviewer gates remain necessary; node-specific acceptance contracts and bounded safe retries are not implemented yet.
- In-flight mutation replay remains prohibited. Manual reconciliation is required after interruption during a running node.
- The 11 `RuntimeWarning` messages in the test suite are expected for tests that explicitly exercise unsafe host execution without an OS sandbox.

---

## V3.4-dev4

### ZX Code v3.4.0-dev4

## Node-level DAG scheduler
- Replaced the single whole-plan ReAct execution prompt with a deterministic sequential scheduler.
- Each ready DAG node is dispatched as an isolated ReAct execution unit.
- Nodes receive only their own task plus bounded evidence from completed prerequisites.
- Node state (`running`, `completed`, `failed`, `blocked`), result, and error metadata are checkpointed after each boundary.
- Failed nodes block dependent descendants; independent nodes remain pending and are not silently marked complete.
- Mutating nodes and delegated sub-agents remain sequential inside the DAG scheduler. No parallel mutation execution was introduced.

## Resume controller
- Added `ZXAgentPro.resume_task_stream()` and CLI command `/resume-task <task-id>`.
- Resume restores the validated serialized graph and skips completed nodes.
- Resume proceeds only from a safe node boundary where no node is recorded as running and no mutation is marked in-flight.
- Interrupted running nodes are refused and require manual workspace reconciliation. The agent does not replay them.
- `/tasks` displays the recovery assessment for each saved task.

## Mutation idempotency
- Mutation journal now persists a tool-call invocation ledger before execution and records the final status afterward.
- Duplicate provider tool-call IDs with identical tool and arguments are suppressed; reuse with different arguments is rejected.
- A call left `in_flight` after a crash is treated as uncertain and is never replayed.
- This is call-ID idempotency within a node journal, not a guarantee of exactly-once semantics for arbitrary external side effects.

## Safety boundaries
- Node execution is sequential; parallel execution remains disabled.
- A process crash during a running node requires manual reconciliation even if the operation may have been read-only. This is deliberately conservative.
- Shell commands and plugins can have external side effects beyond workspace files; the journal cannot reverse or prove exactly-once behavior for those systems.

## Verification
- See `TEST_REPORT_V3.4-dev4.md`.

---

## V3.4-dev3

### ZX Code v3.4.0-dev3

## Crash recovery foundation
- Added `ExecutionCursor` with validated phases, monotonic checkpoint sequence, completed node IDs, current node ID, and mutation-in-flight marker.
- Agent persists cursor checkpoints at planning, execution start, coder completion, verification, review, and terminal boundaries.
- Task state schema advances to v2 while retaining read compatibility for v1 records.
- Added conservative recovery assessment: terminal tasks are inspection-only; interrupted execution/verification/review requires manual reconciliation; pre-execution interruptions require user confirmation before restart.
- `/tasks` now displays the advisory recovery action for each stored task.
- No provider credentials, environment variables, provider objects, or raw conversation/tool transcripts are persisted by the cursor.

## Safety boundary
This release does **not** implement automatic task continuation or node-level DAG scheduling. The current ReAct loop can mutate files between durable phase checkpoints. A crash during that interval is therefore classified as `manual_reconciliation`; the agent must not blindly replay the request or tool calls.

## Verification
- Added cursor serialization, invalid-state, terminal-state, interrupted-mutation, legacy-v1 compatibility, and task-store recovery tests.
- Full test results are recorded in `TEST_REPORT_V3.4-dev3.md`.

---

## V3.3

### ZX Code v3.3.2

## Self-repair integration
- Connected `TaskRepairer` to the ReAct loop for failed verification commands after successful workspace mutations.
- Repair prompt now includes bounded diagnostic evidence, explicit root-cause inspection, minimal-scope correction, and mandatory re-verification.
- Tool output is explicitly treated as untrusted data; common API key, token, password, secret, and bearer patterns are redacted before inclusion in the repair instruction.
- Repair remains bounded to one pass; repeated failure stops execution without erasing successful edits.
- Added three tests covering repair prompt safety, secret redaction, retry limits, and integration with the verification loop.

## v3.3.1 retained changes
- Explicit Docker/Podman runtime selection fails closed instead of silently falling back to another runtime.
- Container commands use `sh -c` so the image toolchain PATH is preserved.
- Default multi-stage sandbox image uses upstream Python/Node/Go toolchains and excludes C/C++ compilers; optional `full` target includes build-essential.
- VM Docker image size reduced from 1,457,732,723 bytes to 877,609,779 bytes (39.8%).
- Docker integration verified as `agentuser` through a fresh Docker group context; workspace writes, read-only root filesystem, and no-network policy tested.

## Known limitations
- Task state is persisted at lifecycle stages, but there is no resume controller.
- Planner DAG dependencies are not yet used by an execution scheduler.
- Repair is a single bounded Coder retry, not a full independent Debugger→Coder→Tester repair cycle.
- Docker group access is root-equivalent on the host; only trusted operators should receive it.
