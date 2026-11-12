# ZX Code v3.3.2

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
