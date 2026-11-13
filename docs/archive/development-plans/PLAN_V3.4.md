# ZX Code Agent v3.4 — Implementation Plan

## Sasaran

Meningkatkan reliability task execution: agent dapat melanjutkan pekerjaan yang terputus dengan aman, mengelola beberapa task, dan mengeksekusi dependency DAG secara deterministik. Tidak menambah UI atau integrasi yang belum dibutuhkan.

## Milestone 1 — Task state v2

Progress dev1: per-task metadata records, active pointer, task listing, atomic writes, task_id validation, workspace/symlink checks, and allow-listed payload persistence are implemented.

Progress dev3/dev4: schema v2, execution cursor, per-task records, atomic write + fsync, restrictive permissions, allow-listed payload, and v1 read compatibility are implemented. Provider objects, environment variables, API keys, and credentials are not persisted.

Remaining: workspace source fingerprint/drift detection and concurrent-save stress tests.

## Milestone 2 — Resume controller

Progress dev4: `/resume-task <task-id>` restores the validated DAG and continues only pending nodes from a safe node boundary. Running nodes are refused for manual reconciliation. Per-node cursor and graph state are persisted.

Remaining: `/task-status` and `/discard-task`; stronger crash-injection tests at every persistence boundary; workspace/source drift reconciliation. `/resume-task` is an explicit user action and only continues from a safe node boundary.

## Milestone 3 — DAG execution

Progress dev4: sequential node-level scheduler dispatches each ready node independently, persists running/completed/failed/blocked status and bounded results, and passes prerequisite evidence to dependents. Mutating nodes remain sequential.

Progress dev2/dev4: graph validation, cycle detection, forward-edge parsing, deterministic topological ordering, ready-node discovery, blocked-descendant propagation, per-node dispatch, and durable node results are implemented. Mutating nodes and delegated sub-agents run sequentially in the DAG scheduler.

Remaining: retries with bounded per-node policy, node-level verification contracts, and safe read-only parallel scheduling after isolation tests.

## Milestone 4 — Repair engine v2

Progress dev4: mutation tool-call ID ledger claims before execution and suppresses duplicate call IDs; uncertain in-flight calls are not replayed. This is not exactly-once protection for arbitrary external side effects.


- Klasifikasi error (syntax, dependency, test, type, runtime, permission, timeout).
- Debugger menghasilkan root-cause hypothesis dan tindakan yang dapat diverifikasi.
- Coder menerapkan patch; Tester menjalankan ulang command yang gagal.
- Batas percobaan dan token/cost budget; no-progress detection.
- Reviewer gate tetap memblokir hasil berisiko.

## Exit criteria v3.4

- Resume pada boundary node tidak mengulang node yang sudah selesai; interrupted running nodes are refused for manual reconciliation.
- Tidak ada resume lintas workspace atau dari state korup.
- DAG dependency dihormati pada seluruh skenario test.
- Benchmark recovery dan task completion dipublikasikan bersama pass rate.
- Seluruh regression suite lulus dan sandbox policy tidak melemah.
