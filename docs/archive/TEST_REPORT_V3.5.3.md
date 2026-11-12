# Test report — ZX Code v3.5.3

## Environment

- Python 3.11 project virtual environment.
- VM Docker daemon socket is inaccessible to the project user; Podman is not installed.

## Verification

| Check | Result |
|---|---|
| Full pytest suite | 367 passed, 11 warnings |
| Recovery/task-state/crash-injection focused suite | 30 passed |
| Compileall | Passed |
| Import smoke | 8 modules imported |
| CLI version | `ZX Code v3.5.3` |

## Added recovery regression coverage

- Duplicate task graph node IDs are rejected.
- Unknown dependency references are rejected.
- Cursor references to unknown completed nodes are rejected.
- Workspace drift is blocked by the centralized recovery assessment.
- Unresolved operation journals block continuation.
- Unreadable journal manifests block continuation.
- Failed tool invocation evidence blocks continuation as uncertain.
- Rollback skip evidence blocks continuation.
- A clean node boundary with a matching fingerprint and no journals remains eligible only for explicit user confirmation.

## Limitations and risks

- Unit tests validate decision logic; they do not simulate an actual VM power loss or kernel crash.
- Docker end-to-end verification was not possible because the VM user cannot access `/var/run/docker.sock`; Podman is absent.
- Advisory locking coordinates ZX Code processes only. External IDEs, shells, daemons, or tools can mutate the workspace without honoring the lock.
- Fingerprints exclude VCS and configured cache/build directories; they are not a cryptographic proof of every external side effect.
- A process can still crash after an external side effect and before recording its result. Such cases require manual reconciliation; no exactly-once guarantee is made.
- Journal manifests are local JSON records, not signed or hash-chained audit logs.

## Conclusion

The available regression suite and local smoke checks passed. Resume decisions now incorporate graph integrity, journal uncertainty, workspace drift, and lock-protected validation. This is a stronger conservative recovery gate, not a claim of complete crash-proof or exactly-once execution.
