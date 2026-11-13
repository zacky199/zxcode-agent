# ZX Code v3.2 — Role-based engineering architecture

## Design principle

A role is a workflow stage with a bounded responsibility, an explicit artifact,
and a provider route. It is not merely a persona prompt. The primary provider
remains a fallback for every role, but a fallback is not represented as an
independent second opinion.

## Workflow

```text
User request
    |
    v
Architect (optional, dedicated model) -> architecture brief
    |
    v
Planner (dedicated or primary) -> task graph
    |
    +--> Tester (optional, dedicated model) -> verification strategy
    |
    v
Coder (dedicated or primary) -> tool-enabled ReAct implementation
    |                         |
    |                         +--> Debugger (dedicated or primary)
    |                              repair analysis after failed verification
    v
Mutation journal closes -> bounded textual diff
    |
    +--> Reviewer (different model required) -> correctness findings
    |
    +--> Security Auditor (different model required) -> security findings
    |
    v
Final report + role results
```

## Role contracts

| Role | Input | Output | Workspace mutation |
|---|---|---|---|
| Architect | User goal + project context | Design constraints and approach | No |
| Planner | User goal + context + architect brief | Ordered task graph | No |
| Coder | Goal + plan + verification strategy | Code changes and execution evidence | Yes, through existing tool policy |
| Debugger | Failure details | Root-cause-oriented repair instruction | No direct mutation |
| Reviewer | Goal + journal summary + bounded diff + coder report | Correctness review | No |
| Tester | Goal + plan + project context | Test strategy / acceptance evidence | No direct mutation |
| Security Auditor | Goal + journal summary + bounded diff + coder report | Security findings | No |

## Independence rules

- A role configured with the same model name as the primary model is not an
  independent reviewer and is skipped for optional advisory/review stages.
- A missing role route falls back to the primary provider.
- Reviewer and Security Auditor never receive shell execution tools and cannot
  edit files. Their output is advisory and is not silently treated as a pass.
- The journal diff omits sensitive paths, symlinks, binary/unreadable files, and
  oversized files; the review is therefore bounded, not a complete security
  proof. Review findings must state when evidence is incomplete.
- Tester supplies a verification strategy. The Coder still executes checks
  through the existing command approval flow; a future release should add a
  separate test executor/report contract.

## Known security limitation

The existing host shell runner still executes with the privileges of the ZX
Code process. Approval prompts, risk heuristics, mutation journals, and
checkpoints are not OS isolation. v3.2 does not claim this has been solved.
The next P0 change is an opt-in Docker/Podman execution backend with a
read-only container root, workspace-only bind mount, dropped capabilities,
no-new-privileges, PID/memory/CPU limits, and network disabled by default.
Host execution must remain visibly labelled as privileged until that backend
is integrated and tested.

## Next implementation sequence

1. OS-level execution isolation and process-tree cleanup.
2. Tester-owned verification execution and machine-readable pass/fail evidence.
3. Reviewer findings schema with severity, file/line, confidence, and gate policy.
4. Persist task graph, role outputs, checkpoints, and resume metadata during a
   task rather than only at request completion.
5. Real-provider soak tests, model fallback, token/cost telemetry, and regression
   suites across Python, TypeScript, Go, and multi-package repositories.
