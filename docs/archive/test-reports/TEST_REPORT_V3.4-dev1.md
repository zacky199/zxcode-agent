# ZX Code v3.4.0-dev1 — Verification report

## Automated tests

- `python -m compileall -q .`: PASS.
- `.venv/bin/python -m pytest -q`: **307 passed**, 11 expected RuntimeWarning messages from tests explicitly opting into unsafe host execution.
- `python main.py --version`: **ZX Code v3.4.0-dev1**.
- New tests cover bounded repair prompts, secret redaction, retry exhaustion, per-task state isolation, task ID validation, allow-listed persistence, corruption reporting, and selective deletion.

## Sandbox integration (VM)

- Default Docker image: 877,609,779 bytes (previous image: 1,457,732,723 bytes; 39.8% smaller).
- Optional `full` image: 1,210,885,565 bytes.
- Python 3.12, Node.js 22/npm, Go 1.24, and Git verified in default container.
- Workspace write-through succeeded; root filesystem write failed as expected.
- Outbound network blocked with `--network=none`.
- `SandboxExecutor` ran as `agentuser` using `sg docker -c` without sudo.

## Not yet implemented

- Task resume: records currently store allow-listed metadata, not full conversation/tool execution cursor.
- DAG dependency scheduler.
- Independent Debugger→Coder→Tester repair cycle and repair from review findings.
- Full-repository static analysis or formal security proof.
