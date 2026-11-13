# Test report — ZX Code v3.5.0

## Added coverage

- Acceptance passes only when the loop exits cleanly, a non-empty result exists, and the mutation journal closes.
- Acceptance rejects fatal execution, empty output, and journal-close failure.
- Acceptance evidence is JSON-safe for checkpoint persistence.
- Existing DAG serialization retains compatibility because acceptance metadata is optional.

## Verification performed in the VM

- `python -m compileall -q .`: PASS.
- `python main.py --version`: PASS (`ZX Code v3.5.0`).
- Core module imports: PASS.
- Focused acceptance contract cases (pass, empty output, fatal loop, unclosed journal): PASS.
- DAG serialization and task-checkpoint acceptance evidence roundtrip: PASS.
- Full pytest suite: NOT RUN. The VM has no pytest installation and the package is not cached; network access is disabled.

This release is not represented as having a full-suite pass. Run `pytest -q` in a development environment with project test dependencies installed before production deployment.
