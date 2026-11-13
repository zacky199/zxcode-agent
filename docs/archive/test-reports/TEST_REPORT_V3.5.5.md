# ZX Code v3.5.5 — Test Report

## Results

- Full pytest suite: **378 passed, 11 warnings**.
- Focused crash consistency, mutation journal and task-state suite: **44 passed**.
- Python compileall: passed.
- Import smoke: 8 core modules passed.
- CLI version: ZX Code v3.5.5.

## Process-termination scenarios

1. Process exits after audit-chain append but before manifest replacement: recovery returns unknown.
2. Process exits after rollback has restored a file but before final rollback outcome persistence: workspace reflects the restore, but journal remains rollback_in_progress and recovery reports uncertain.
3. Process exits after per-task record replacement but before active-task pointer replacement: the per-task record contains the new state and the active pointer retains its prior valid state.
4. Process exits before checkpoint atomic publication: no final-name checkpoint ZIP is published.
5. Process exits after checkpoint replacement but before directory fsync: the visible final-name ZIP is complete and CRC-valid.

## Additional safety coverage

- Rollback does not touch workspace files when write-ahead rollback intent cannot be persisted.
- A completed rollback is recognized as a terminal journal operation.
- Existing recovery, mutation, checkpoint, task-state and agent tests remain passing.

## Warnings and unverified environments

- 11 existing RuntimeWarnings indicate command execution inherits ZX Code process privileges because no OS sandbox is active.
- Process-kill tests use os._exit and validate process termination ordering; they do not prove durability under actual power loss or kernel crash.
- No VM hard reset, physical power-cut, or filesystem-specific durability matrix was run.
- Docker E2E was not run because the VM account cannot access the Docker daemon socket; Podman is unavailable.
- No exactly-once guarantee is made for external commands, APIs, plugins, or other side effects.
