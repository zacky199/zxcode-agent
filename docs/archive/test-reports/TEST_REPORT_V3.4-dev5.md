# Test report — ZX Code v3.4.0-dev5

## Environment

- Python: 3.11.16
- Test runner: pytest from the existing project verification virtual environment
- OS sandbox warnings: expected in tests explicitly opting into unsafe host execution

## Results

- `python -m compileall -q .`: PASS
- `python main.py --version`: PASS (`ZX Code v3.4.0-dev5`)
- Full test suite: **332 passed, 11 warnings**
- New tests: workspace fingerprint drift detection; checkpoint metadata exclusion; bounded fingerprint failure; end-to-end resume refusal after an external workspace edit.

## Recovery behavior

- Exact workspace match at a durable node boundary: resume may proceed.
- Workspace drift: automatic resume is blocked.
- Fingerprint unavailable or absent (including older task records): automatic resume is blocked and manual reconciliation is required.
- Running/interrupted node: still refused; no automatic replay.
- ReAct iteration exhaustion or unresolved verification: node is failed, not completed.

## Scope note

These tests do not prove crash consistency under every possible power-loss point or protect against concurrent external writes after the fingerprint comparison. They verify deterministic application-level recovery gates and regression behavior.
