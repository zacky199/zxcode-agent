# ZX Code v3.3 — Change plan

## Baseline
- Input: v3.2 source ZIP and `ROLE_ARCHITECTURE_V3.2.md`.
- v3.1 remains untouched; v3.3 is built in a separate directory.
- Preserve the seven existing role identifiers and provider routing.

## Work sequence
1. Replace the misleading host-only sandbox with Docker/Podman execution, fail-closed when unavailable, and explicit unsafe host opt-in only.
2. Add a Tester-owned runner that invokes approved verification commands through the same OS sandbox, independently of Coder's ReAct loop.
3. Require machine-readable Reviewer/Security Auditor findings; block on critical/high and treat malformed or missing gate output as inconclusive/blocking.
4. Add atomic, workspace-bound task-state persistence with a narrow payload allow-list that excludes credentials.
5. Add tests for runtime unavailable, host opt-in, isolation flags, Tester execution, task-state recovery/corruption, and review gate outcomes.
6. Run full test suite and compile checks; document any unverified runtime/image/platform limitations.
