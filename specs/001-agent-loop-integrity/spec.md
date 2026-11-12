# Spec: Agent Loop and Mutation Integrity

**Status:** approved for implementation in `/workspace/zxcode-agent-next` only
**Baseline:** frozen copy supplied as e24dad at `/workspace/zxcode-agent-frozen-20261106-1232`
**Protected:** `/workspace/zxcode-agent`, the frozen baseline, and the final ZIP are read-only.

## Goal

Improve ZX Code's agent-loop correctness and mutation integrity without changing its modular architecture or removing autonomous edits inside the selected workspace. The change has two tracks: (1) planner routing, loop guard, and history compaction; (2) task-scoped mutation journals, safe rollback, and serialized shell approval.

## Track 1 — Agent loop

### User stories

- As an operator using role-based providers, I want planning requests sent to the configured planner backend while execution remains on the main provider.
- As a user, I want a repeated-call guard to stop genuine no-progress loops without mistaking a repeated call after progress for a loop.
- As a user, I want context compaction to retain valid tool-call history and my latest request so the provider can continue safely.

### Acceptance criteria

1. `TaskPlanner` calls the planner-role provider selected by `RoleRouter`; execution streaming continues to use the main provider. If no planner role is configured, planner calls fall back to the main provider. Tests use distinct fake providers and prove the route of each call.
2. The loop guard counts only a consecutive run of the same canonical tool name and arguments. A different call breaks the streak; repeated identical consecutive calls still stop at the configured threshold.
3. Compaction occurs only at complete user-delimited conversation boundaries. Any retained assistant `tool_calls` message retains every matching tool result (`tool_call_id`); no tool result is left without its call. The most recent user message is always retained. If the newest complete turn itself is larger than the nominal retention budget, keep it whole rather than emit an invalid partial turn.

## Track 2 — Integrity and security

### User stories

- As a user, I want every native, Markdown, and shell mutation to have a task-local recovery record before it can touch files.
- As a user, I want a failed/partial mutation to undo only its own still-unchanged effects, not my prior work or concurrent external changes.
- As a user, I want subagents to have independent journals and no ability to roll back another task's files.
- As an operator, I want each shell approval serialized and bound to the exact command that is executed.
- As a user, I want successful edits to survive test/build failures, receive one repair pass, and then get a diff summary and manual undo guidance if verification still fails.

### Acceptance criteria

1. A mutation cannot start unless its task-local journal/checkpoint has been initialized and its required pre-state has been recorded. Journal/checkpoint failure fails closed before the native tool, Markdown block, or shell command touches files; for shell, approval is not requested and no subprocess is started when checkpoint setup fails.
2. Every top-level task and every subagent has a distinct journal/owner. Journal records are scoped to paths changed by that operation; no workspace-wide snapshot restore is used as a rollback authority.
3. Automatic rollback is limited to failed/partial native mutation operations. A native `write_file`/`edit_file` target is rollback-owned only when its actual content matches the deterministic expected output, its identity differs from the pre-state as expected for the built-in atomic writer, and its post-state still matches immediately before restoration. Native writes stage output, check the expected pre-state at the commit boundary, quarantine an existing target, and use a no-overwrite install; a changed target aborts the commit and is preserved/reported. When replacing an existing file, retain its displaced inode in the private task journal so a process writing through an already-open handle cannot lose late data; check it at operation finish and journal close, and report any late write plus the retained path. If ownership is missing/overlapping or any check is inconclusive, preserve it and report it. Shell workspace before/after deltas do not prove authorship: never automatically roll back shell deltas, preserve and report them as `CONFLICT/AMBIGUOUS` even when the command fails, and warn on successful commands that leave changed paths.
4. A failed test/build/verification command after successful edits leaves those edits in place and allows one repair pass. If verification still fails after that pass, stop with a clear report and manual undo guidance; do not restore successful task edits automatically.
5. While verified OS-level isolation or containment is not active in the current Sandbox runtime, native `write_file`/`edit_file` must fail closed at both `BuiltinTools` and `ToolEngine`; Markdown `file`/`patch` must fail through the same policy. The model or Markdown arguments cannot enable these operations. This applies to existing and missing parent directories, and a missing parent is not created. Markdown `bash` and shell remain governed by the existing exact-command approval policy and retain the process's OS permissions.
6. Shell approval and execution are serialized as one operation. Each approval authorizes exactly one command string, and the executed command is the identical string that was reviewed. Missing, rejected, or mismatched approval fails closed.
7. On task completion, emit a concise summary of file changes, ambiguous preserved shell paths, rollback skips, retained original-inode paths, and a reference to the available manual undo material. A shell post-scan failure is recorded and summarized as `CONFLICT/UNCERTAIN`, never as an inferred no-delta result. The CLI presents that summary.
8. Shell and Python plugins continue to run with the process's OS privileges. Checkpointing and the task journal are recovery records, not a sandbox, and do not promise to undo effects outside the workspace or arbitrary external side effects. Retained original inodes remain in the private journal for inspection/recovery and require separate cleanup to reclaim storage. Cleanup is manual: stop processes that may hold old handles, inspect/archive the manifest and late-write data, then remove only selected retained entries; journal close is not proof that handles are gone, and there is no age-based auto-deletion.
9. The deprecated public `register_builtin_tools` bridge registers read-only handlers only. `write_file`, `edit_file`, and `run_command` must be invoked through `ToolEngine` so journal and approval gates cannot be bypassed through the legacy registry API.
10. Native mutations capture workspace and parent directory `device+inode` identities before execution, pin the existing chain with directory handles, use no-follow dirfd-relative target operations, and recheck the chain at commit and restore boundaries. Missing parent directories are not created or adopted: if the checkpoint recorded a missing parent, native mutation fails closed, including when a directory appears externally afterward. A changed or unverifiable chain aborts commit or skips restore and is reported as conflict; platforms without required dirfd support fail closed. Parent-swap interleaving regressions prove replacement-directory sentinels are not overwritten.

## Scope exclusions

Do not change `max_iterations`, planner behavior for greetings, session persistence, naming, dependency pinning, or other backlog items. Do not integrate changes into the canonical root automatically. Do not claim shell/plugin isolation or guarantee rollback of effects outside the recorded workspace journal. The acceptance does not introduce a new command-danger classifier; existing shell approval remains the authorization boundary.

## Decisions and conservative assumptions

- “Consecutive duplicate” means adjacent calls with the same tool name and canonicalized arguments; any different call resets the streak.
- A successful prior mutation is not rolled back because a later test, build, provider call, or unrelated tool fails.
- When ownership or current-state checks are uncertain, preserving the current file and reporting the skipped restore is safer than restoring.
- The single repair pass is scoped to a post-mutation verification failure; repeated verification failure after that pass ends the task with the workspace edits preserved.
