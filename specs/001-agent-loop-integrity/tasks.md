# Tasks: Agent Loop and Mutation Integrity

## 0. Baseline and safeguards

- [x] Copy frozen e24dad baseline into `/workspace/zxcode-agent-next` without modifying source; verify identical manifest.
- [x] Run baseline suite with `python3 -m pytest -q` (152 passed).
- [x] Keep canonical root, frozen copy, and prior ZIP read-only; produce a final changed-file manifest and patch.

## 1. Planner routing, anti-loop, and compaction

- [x] Add tests for planner-role completion routing and fallback to the main provider.
- [x] Wire `TaskPlanner` to `_planner_complete`; retain the main-provider shim for the repairer.
- [x] Test consecutive duplicate calls and verify an intervening distinct call resets the repeat streak.
- [x] Implement canonical consecutive-call streak.
- [x] Test compaction across user-turn boundaries, complete assistant tool-call/result groups, no orphan tool messages, and latest-user retention.
- [x] Implement complete-group compaction; retain an oversized latest turn structurally intact.

## 2. Task-local journal and fail-closed mutation gates

- [x] Test private initialization, per-operation pre/post fingerprints, created/overwritten/deleted paths, fail-closed initialization, and persistent manual-undo material.
- [x] Implement task/owner-scoped journal and guarded per-operation restore; do not use workspace-wide snapshot restore as task rollback authority.
- [x] Test native-tool journal/checkpoint failure blocks handler invocation and file changes.
- [x] Gate native `write_file`, `edit_file`, and `run_command` before mutation; preserve read-only behavior and sensitive-path checks.
- [x] Test shell checkpoint failure blocks before approval callback or subprocess launch.
- [x] Test Markdown parity for `file`, `patch`, and `bash`; failed journal setup blocks mutation.
- [x] Gate each Markdown mutation through the shared journal API and report blocked/partial outcomes.
- [x] Test post-scan failure is summarized as `CONFLICT/UNCERTAIN`, without claiming a zero-delta result.
- [x] Deprecate `register_builtin_tools` as read-only-only and deny direct built-in writes without the journal guard.

## 3. Ownership, rollback policy, and subagents

- [x] Test failed/partial operation restores only its owned paths when unchanged.
- [x] Test external edits, inode replacement, unrelated new files, and ambiguous ownership are preserved/reported; recheck immediately before restore commit.
- [x] Regress external shell write between begin/finish while the command is a no-op; preserve and report the ambiguous delta.
- [x] Regress write/edit external changes at the staged commit boundary; abort no-overwrite install, preserve target, and report conflict.
- [x] Require native expected-output and pre-state identity checks before treating a failed write/edit as rollback-owned.
- [x] Retain displaced native inodes in the private journal; test stale open-handle writes remain recoverable and are reported at finish/close.
- [x] Test successful edits survive verification failures; permit exactly one task repair pass, even if an intermediate verification succeeds, and report manual undo after a later failure.
- [x] Implement per-path and ancestor/descendant ownership coordination; reject overlapping active operations, including same-owner concurrency.
- [x] Test independent subagent journals and verify one task's failure cannot erase another owner's successful edit.
- [x] Remove run-level consecutive-failure/task-snapshot rollback that could restore successful work.

## 4. Shell approval and reporting

- [x] Test concurrent approval/execution serialization and exact-command approval non-reuse.
- [x] Serialize the approval/execution critical section in `SandboxExecutor`; preserve fail-closed behavior across native, Markdown, and subagent routes.
- [x] Report successful shell workspace deltas as ambiguous; never automatically roll back shell deltas.
- [x] Test final summaries for changed paths, rollback skips, repair result, and manual undo reference.
- [x] Emit one concise task diff-summary event and render it in the CLI.
- [x] Document shell/plugin OS privileges, bounded workspace journaling, and that recovery is not sandboxing.

## 5. Final verification

- [x] Run focused TDD suites; shell provenance and native output regressions were observed failing before their implementation slices.
- [x] Earlier implementation QA in `-next` before the parent-identity fix: **188 full-suite tests passed**, **38 explicit security tests passed**, and `compileall` passed; `pyflakes` was unavailable and is not claimed as run.
- [x] Snapshot 2011 QA: **188/188 passed**, but sign-off was withheld because the parent-directory replacement race remained. No QA or Sentinel sign-off is claimed for that snapshot, and nothing was integrated.

## 6. Parent-directory identity and retained-inode lifecycle

- [x] Add TDD parent-swap regressions at native commit and rollback/restore boundaries; also verify a directory appearing after a missing-parent checkpoint is not adopted and its sentinel bytes/inode remain unchanged.
- [x] Pin workspace and parent directory chain by dirfd plus `device+inode`; use no-follow dirfd-relative native target operations and fail closed when unsupported or uncertain.
- [x] Recheck parent identity before/after native commit and before/after restore; report conflict and abort/skip when the pinned chain no longer matches.
- [x] Document retained-inode cleanup as manual, requiring process shutdown and review/archive before selected deletion; no age-based automatic cleanup.
- [x] Updated `-next` QA after the final fail-closed policy: **130 focused tests**, **191 full-suite tests**, **139 security/journal/tool tests**, and `compileall` passed.
- [x] Freeze `/workspace/zxcode-agent-frozen-20261107-2046`; its manifest still matched after QA (**115 files**, **30 directories**, **0 symlinks**).
- [x] Snapshot QA: **191/191 full suite**, **139 security/journal/tool tests**, and `compileall` passed.
- [x] Independent read-only Sentinel review of snapshot 2046: **PASS**, no P1/P2 blockers; the reviewer did not run tests.
- [x] Leave canonical root and earlier snapshots untouched; integration remains deferred.

## Explicitly deferred

`max_iterations`, greeting fast-path/planner skipping, session persistence, naming consistency, and dependency pinning remain separate backlog and are not part of this task.

## 7. Approved native file-mutation containment amendment

- [x] Add failing policy tests for direct `BuiltinTools`, `ToolEngine`, and Markdown `file`/`patch` actions, including existing/missing parents, handler bypass attempts, no file/content leak, and no mutation; observed the new tests fail before the guard.
- [x] Add a static fail-closed native file-mutation policy at both `BuiltinTools` and `ToolEngine`; model/Markdown arguments cannot override it.
- [x] Ensure denied Markdown file/patch actions do not create checkpoint/journal files solely to report denial; preserve shell approval and process-privilege behavior.
- [x] Update README UX/security notes and the spec/plan to state native writes are unavailable in this Sandbox, missing parents are not created, and journals are not an OS sandbox.
- [x] Preserve parent-missing, parent-swap (commit and restore), and checkpoint/journal failure regressions at the relevant lower-level boundary.
- [x] Run focused tests offline (**129 passed**) and full suite (`python3 -m pytest -q`: **204 passed**); keep `/workspace/zxcode-agent`, frozen QA copies, ZIP files, and canonical integration untouched.
