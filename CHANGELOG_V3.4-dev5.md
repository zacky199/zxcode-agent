# ZX Code v3.4.0-dev5

## Reliability and recovery hardening

- Added bounded workspace fingerprints to task checkpoints (SHA-256 over file contents, paths, modes, directory structure, and symlink targets).
- Resume now compares the live workspace with the last durable checkpoint and refuses automatic continuation when drift is detected.
- Fingerprinting fails closed when traversal is incomplete, the workspace exceeds 20,000 files / 512 MiB, or a file changes while being read.
- Excludes VCS metadata, task checkpoint metadata, virtual environments, dependency directories, and common interpreter/test caches to avoid self-referential or volatile snapshots.
- ReAct iteration exhaustion and unresolved post-mutation verification now mark the active node as failed instead of allowing the DAG scheduler to mark it completed.
- Added regression coverage for fingerprint drift, checkpoint metadata exclusion, fingerprint limits, and end-to-end resume refusal after external workspace edits.

## Compatibility

- Task-state schema advanced to v3; v1 and v2 records remain readable.
- Older task records without a complete workspace fingerprint are not eligible for automatic resume and require manual reconciliation.
- Existing v3.4 DAG scheduling and mutation invocation ledger behavior is retained.

## Known limitations

- Fingerprints are bounded snapshots, not filesystem locks. A concurrent external writer can still change files after the comparison and before execution; run ZX Code in a workspace not being edited concurrently.
- Ignored directories such as `.git`, `.venv`, `node_modules`, and caches are not fingerprinted. Source manifests and lockfiles remain fingerprinted.
- A node may still produce a misleading textual report despite non-empty output. Independent Tester / Reviewer gates remain necessary; node-specific acceptance contracts and bounded safe retries are not implemented yet.
- In-flight mutation replay remains prohibited. Manual reconciliation is required after interruption during a running node.
- The 11 `RuntimeWarning` messages in the test suite are expected for tests that explicitly exercise unsafe host execution without an OS sandbox.
