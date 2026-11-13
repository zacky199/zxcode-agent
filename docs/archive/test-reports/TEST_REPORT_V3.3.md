# ZX Code v3.3.2 — Verification report

## Automated tests

- `python -m compileall -q .`: PASS.
- `.venv/bin/python -m pytest -q`: **302 passed**, 11 expected RuntimeWarning messages from tests explicitly opting into unsafe host execution.
- `python main.py --version`: **ZX Code v3.3.2**.
- New repair tests cover bounded prompt construction, untrusted terminal output handling, common secret redaction, retry exhaustion, and ReAct-loop integration.

## Sandbox integration (VM)

- Default Docker image: 877,609,779 bytes (previous image: 1,457,732,723 bytes; 39.8% smaller).
- Optional `full` image: 1,210,885,565 bytes.
- Python 3.12, Node.js 22/npm, Go 1.24, and Git commands verified in the default container.
- Workspace write-through succeeded; root filesystem write failed as expected.
- Outbound network was blocked using `--network=none`.
- `SandboxExecutor` ran as `agentuser` using `sg docker -c` without sudo; this confirms Docker group execution in a fresh group context, not that the pre-existing login shell had refreshed supplementary groups.

## Known limitations

- Automatic resume of interrupted tasks is not implemented. `TaskStateStore` persists metadata but does not restore the ReAct conversation or execution cursor.
- Planner DAG dependencies are currently flattened into plan descriptions and do not control scheduling.
- Self-repair is bounded to one retry after a verification failure following successful mutations; reviewer/security findings do not trigger automatic repair.
- Review scope is bounded by the mutation journal; this is not a full-repository static analysis or formal security proof.
- Docker daemon and docker-group access remain privileged host trust boundaries.
