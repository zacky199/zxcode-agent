# ZX Code v3.5.4 — Test Report

## Results

- Full pytest suite: **371 passed, 11 warnings**.
- Recovery integrity + task-state focused suite: **22 passed**.
- Python compileall: passed.
- Import smoke: 8 core modules passed.
- CLI version: ZX Code v3.5.4.
- Release ZIP CRC verification: passed.
- Release patch: git apply --check and git apply against clean v3.5.3 baseline passed.
- Patched tree and release source SHA-256 comparison: **178 files identical**.

## Added regression coverage

- Valid hash-chained journal is accepted.
- Manifest payload tampering is rejected.
- Truncated audit chain is rejected.
- Legacy v1 journal without integrity chain is classified unknown.
- Existing v3.5.3 task-state/recovery tests remain passing.

## Warnings and unverified environments

- 11 existing RuntimeWarnings report unsafe host execution because no OS sandbox is active; these are not test failures.
- Docker E2E was not run because the VM account does not have access to the Docker daemon socket; Podman is unavailable.
- No physical power-loss, kernel-crash, or filesystem fault-injection campaign was run.
- Hash chain is local tamper-evident bookkeeping, not a digital signature. A privileged actor able to rewrite the entire journal and chain can recompute hashes.
- No exactly-once guarantee is made for external commands, plugins, APIs, or other side effects.
