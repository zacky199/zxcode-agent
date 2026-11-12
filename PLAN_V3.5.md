# ZX Code v3.5 reliability plan

## Delivered in 3.5.0
- Execution-level acceptance contract for DAG nodes.
- Acceptance evidence persisted with task graph state.
- Safe handling of mutation-journal close failures: node fails closed and is not marked completed.
- Task checkpoint inspection and metadata-only discard commands.
- Compatibility with older task records where acceptance evidence is absent.

## Explicitly not claimed
- Semantic correctness is not proven by the execution contract.
- Workspace fingerprints do not lock external editors/processes.
- In-flight mutations after a crash still require manual reconciliation.
- General exactly-once execution is not guaranteed for shell commands or remote side effects.

## Next reliability work
1. Crash-injection tests at every durable journal/checkpoint boundary.
2. Workspace-level advisory locking for concurrent ZX Code tasks, with clear limitations for external processes.
3. Per-node verification plans with sandboxed, explicit test commands and persisted exit-code evidence.
4. Bounded node retries only after classifying side effects and requiring a fresh acceptance run.
5. CI matrix across supported Python versions and Docker/Podman runtimes.
