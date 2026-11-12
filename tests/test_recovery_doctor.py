import json

from execution.recovery_doctor import build_recovery_report
from execution.task_state import TaskStateStore


def test_recovery_doctor_is_read_only_and_reports_safe_boundary(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    store = TaskStateStore(tmp_path)
    store.save("doctor-safe", "planned", {
        "goal": "inspect safely",
        "task_graph": {"nodes": [
            {"id": "inspect", "description": "inspect", "dependencies": [], "status": "completed"},
            {"id": "implement", "description": "implement", "dependencies": ["inspect"], "status": "pending"},
        ]},
    })
    before = (tmp_path / "source.py").read_bytes()
    report = build_recovery_report(store, "doctor-safe")
    after = (tmp_path / "source.py").read_bytes()

    assert report["dry_run"] is True
    assert report["mutations_performed"] is False
    assert report["overall"] == "safe_boundary"
    assert report["recommended_action"] == "restart_from_user_confirmation"
    assert report["node_counts"]["completed"] == 1
    assert report["node_counts"]["pending"] == 1
    assert before == after
    assert any("aggregate-only" in item for item in report["limitations"])


def test_recovery_doctor_blocks_inflight_mutation(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("doctor-interrupted", "executing", {
        "goal": "change safely",
        "execution_cursor": {
            "phase": "executing", "current_node_id": "write", "completed_node_ids": [],
            "interrupted_node_id": "write", "mutation_started": True, "sequence": 2,
        },
        "task_graph": {"nodes": [{"id": "write", "description": "write", "dependencies": [], "status": "running"}]},
    })
    report = build_recovery_report(store, "doctor-interrupted")
    assert report["overall"] == "blocked"
    assert report["recommended_action"] == "manual_reconciliation"
    assert report["mutations_performed"] is False


def test_recovery_doctor_surfaces_missing_record(tmp_path):
    store = TaskStateStore(tmp_path)
    report = build_recovery_report(store, "not-here")
    assert report["overall"] == "blocked"
    assert report["recommended_action"] == "do_not_resume"
    assert report["checks"][0]["status"] == "missing"
    assert report["mutations_performed"] is False


def test_recovery_doctor_report_is_json_serializable(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("doctor-json", "planned", {"goal": "json"})
    report = build_recovery_report(store, "doctor-json")
    encoded = json.dumps(report, sort_keys=True)
    assert '"dry_run": true' in encoded


def test_recovery_assessment_rejects_unhashable_cursor_phase_and_node_status(tmp_path):
    import pytest
    from execution.resume import ExecutionCursor, assess_recovery

    with pytest.raises(ValueError, match="invalid execution cursor"):
        ExecutionCursor.from_dict({"phase": []})

    decision = assess_recovery({
        "stage": "planned",
        "payload": {"task_graph": {"nodes": [{
            "id": "node", "dependencies": [], "status": {"unexpected": "mapping"}
        }]}}
    })
    assert decision["status"] == "invalid"
    assert decision["action"] == "do_not_resume"
