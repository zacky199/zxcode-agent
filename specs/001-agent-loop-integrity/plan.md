# Plan: Agent Loop and Mutation Integrity

## Context

Implementation is confined to `/workspace/zxcode-agent-next`, copied from the supplied frozen e24dad baseline. The source copy has no `.git` metadata, so verification will compare files against the baseline and will not imply a clean Git working tree. Canonical/frozen/ZIP artifacts remain untouched.

## Approach

### A. Agent-loop correctness

1. Connect `TaskPlanner` to `ZXAgentPro._planner_complete`; leave `_llm_completion_shim` available for the repairer. Test with distinct fake planner and main providers, including planner-role fallback.
2. Replace cumulative `call_counts` with a canonical last-call key and consecutive streak. Keep the configured threshold behavior, but reset on any different call.
3. Add a history validator/grouping helper for compaction. Compact only complete user-delimited groups, verify assistant tool-call IDs have corresponding tool results, preserve the latest user request, and keep a too-large latest group intact instead of splitting it.

### B. Mutation journal and recovery

1. Add a private, task-scoped journal API in `execution/` that records each operation's before/after state, owner ID, changed paths, and fingerprints. Journal initialization and pre-state capture must complete before the mutation handler is invoked. Store undo material under the existing private checkpoint area with restrictive permissions; do not ZIP the entire workspace before every mutating call.
2. Route top-level native tools and Markdown blocks through the same journal begin/finish boundary. Keep reads outside the mutation path. Preserve current built-in path containment, sensitive-path, symlink, and hard-link checks.
3. Give each subagent its own journal scope and owner. Use shared coordination for overlapping paths; reject or conservatively report collisions rather than allowing one task to claim another's changes. An unproven owner or inconclusive fingerprint never grants rollback authority.
4. For a failed/partial operation, restore only its recorded paths whose current fingerprints still match that operation's post-state. Preserve and report externally changed/uncertain paths. A successful operation remains intact even if a later test/build/provider action fails.
5. Serialize shell approval and execution in `SandboxExecutor` with a per-executor lock; pass the exact command through authorization and execution without normalization or reuse of a prior approval. Preserve fail-closed behavior.
6. Allow one repair pass after a post-mutation verification failure. A repeated verification failure ends with a report and manual undo guidance; it does not trigger task-wide rollback.
7. Emit a task completion diff summary from the journal and render it in the CLI. Clearly state that shell/plugin execution still has OS-level process privileges and journal recovery is not a sandbox.

### C. Approved native file-mutation containment amendment

1. The current Sandbox has no verified OS-level isolation/containment capability for native workspace writes. Keep native `write_file`/`edit_file` statically disabled and return a generic denial before inspecting paths/content, creating a journal, or invoking a registered handler.
2. Enforce the same policy at both `BuiltinTools` and `ToolEngine`; do not accept model-supplied arguments or Markdown content as an override. Markdown `file`/`patch` route through the same denial and do not create checkpoint/journal state solely to report the denial.
3. Preserve shell approval and execution behavior unchanged. Explicit approval does not sandbox shell; shell retains the process's OS privileges. Document that journals/checkpoints aid recovery but are not an OS sandbox.
4. Keep the existing parent-missing, parent-swap, and checkpoint/journal failure regressions. Where public file mutation is no longer reachable, exercise the parent-identity and rollback primitives directly at the journal/filesystem layer instead of weakening the public guard.

## Planned areas

- `core/agent.py`: planner callback wiring, repeat guard, safe compaction, task journal lifecycle, repair outcome, final summary.
- `execution/`: journal data structures, path snapshots/fingerprints, guarded operation rollback; preserve legacy snapshot validation but do not use broad restore for automatic task rollback.
- `tools/engine.py`, `tools/builtin.py`, and `tools/mutation_policy.py`: shared fail-closed native file-mutation policy and Markdown routing; retain shell journal/approval gates.
- `execution/sandbox.py` and `main.py`: serialized exact-command approval/execution and safe user-facing wording.
- `core/subagent.py`: isolated owner/journal scopes and conservative overlap coordination.
- `ui/console.py`: render final change summary and manual undo information.
- `tests/`: focused tests for routing, compaction, journals, rollback ownership, subagents, concurrent approvals, repair behavior, reporting, and the disabled native file-mutation boundary.
- `README.md` and `specs/`: document the current UX/security boundary and approved amendment.

## TDD sequence

1. Add failing tests for planner routing, consecutive-repeat reset, and compaction structure; run focused tests to confirm intended failures.
2. Implement the smallest Track A changes; run focused then full tests.
3. Add failing tests for journal initialization failure, failed/partial mutation recovery, external-edit preservation, successful-edit preservation, and Markdown parity.
4. Implement the journal API and native/Markdown integration; run focused tests.
5. Add subagent owner/overlap tests and serialized exact-command shell tests; implement their boundaries.
6. Add repair-pass/diff-summary tests and UI behavior; implement final reporting.
7. Run the full suite through `python3 -m pytest -q`, any available lint/CI checks, inspect final diff against the frozen baseline, and verify protected directories remain unchanged.
8. For the approved amendment, first add failing tests for direct built-ins, `ToolEngine`, Markdown `file`/`patch`, existing/missing parents, and no content leak or mutation; then add the shared guard and verify the parent-missing/swap/checkpoint regressions still exercise their lower-level guarantees.

## Risks and mitigations

- **Shell effects are broader than Python file tools.** Capture/restore is scoped to observable workspace files; never claim isolation, and never touch paths with uncertain ownership. The user must still approve each exact command.
- **Concurrent subagents can overlap.** Establish per-path ownership coordination; reject ambiguous overlaps and preserve files rather than guessing.
- **Atomic compare-and-restore races.** Re-check path identity/fingerprint immediately before restoration and use atomic replacement; if the check is inconclusive, skip and report.
- **Journal I/O failure.** Fail closed before invoking mutation handlers.
- **Large workspaces.** Record target-specific native file state and only bounded workspace deltas for shell operations; avoid per-mutation full ZIP archives. If required shell state cannot be captured reliably, block the command rather than claim recoverability.

## Verification baseline

`pytest -q` did not collect because the console entry point omitted the project root from `sys.path`; `python3 -m pytest -q` succeeds with **152 passed** on the untouched work copy. Use the module invocation for subsequent comparisons.
