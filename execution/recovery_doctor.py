"""Read-only recovery diagnostics for persisted ZX Code tasks.

The doctor deliberately performs no repair, rollback, resume, or workspace write.
It reports aggregate workspace drift because earlier checkpoints store only a
whole-workspace fingerprint, not per-file baseline hashes.
"""
from __future__ import annotations

from typing import Any


REPORT_VERSION = 1


def build_recovery_report(store: Any, task_id: str | None = None) -> dict:
    """Build a deterministic, read-only diagnostic report for one task."""
    loaded = store.load(task_id)
    if loaded.get("status") != "stored":
        status = loaded.get("status", "unknown")
        return {
            "report_version": REPORT_VERSION,
            "dry_run": True,
            "mutations_performed": False,
            "task_id": task_id,
            "overall": "blocked",
            "recommended_action": "do_not_resume",
            "checks": [{"name": "task_record", "status": status,
                        "detail": "Task checkpoint is missing, corrupt, or incompatible."}],
            "limitations": ["No recovery action was attempted."],
        }

    state = loaded["state"]
    actual_id = state.get("task_id")
    payload = state.get("payload", {})
    graph = payload.get("task_graph", {})
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    nodes = nodes if isinstance(nodes, list) else []
    cursor = payload.get("execution_cursor", {})
    cursor = cursor if isinstance(cursor, dict) else {}
    decision = store.assess_recovery(actual_id)

    counts = {key: 0 for key in ("pending", "ready", "running", "completed", "failed", "blocked", "unknown")}
    for node in nodes:
        if isinstance(node, dict):
            status = node.get("status", "pending")
            key = status if isinstance(status, str) and status in counts else "unknown"
            counts[key] += 1
        else:
            counts["unknown"] += 1

    journal = decision.get("journal", {}) if isinstance(decision, dict) else {}
    workspace = decision.get("workspace", {}) if isinstance(decision, dict) else {}
    checks = [
        {"name": "task_record", "status": "ok", "detail": "Task checkpoint loaded and workspace-bound."},
        {"name": "execution_cursor", "status": "ok" if cursor else "not_recorded",
         "detail": f"phase={cursor.get('phase', 'not_recorded')}; sequence={cursor.get('sequence', 'n/a')}"},
        {"name": "task_graph", "status": "ok" if not counts["unknown"] else "warning",
         "detail": f"{len(nodes)} nodes; " + ", ".join(f"{k}={v}" for k, v in counts.items() if v)},
        {"name": "mutation_journal", "status": journal.get("status", "not_checked"),
         "detail": journal.get("reason", "Journal state was not independently cleared by the recovery gate.")},
        {"name": "workspace_fingerprint", "status": workspace.get("status", "not_checked"),
         "detail": workspace.get("reason", "Fingerprint comparison was not available for this recovery state.")},
        {"name": "resume_gate", "status": decision.get("status", "unknown"),
         "detail": decision.get("reason", "No recovery decision was returned.")},
    ]

    action = decision.get("action", "do_not_resume")
    overall = "safe_boundary" if action == "restart_from_user_confirmation" else "blocked"
    limitations = [
        "This report is read-only: it does not resume, roll back, repair, or modify task files.",
        "Workspace drift is aggregate-only; historical task records do not contain per-file baseline hashes.",
        "A matching fingerprint is not a lock against subsequent external edits.",
        "External commands, APIs, plugins, and side effects cannot be proven exactly-once by this journal.",
    ]
    return {
        "report_version": REPORT_VERSION,
        "dry_run": True,
        "mutations_performed": False,
        "task_id": actual_id,
        "stage": state.get("stage", "unknown"),
        "updated_at": state.get("updated_at", ""),
        "overall": overall,
        "recommended_action": action,
        "checks": checks,
        "node_counts": counts,
        "limitations": limitations,
    }
