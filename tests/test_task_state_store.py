import json

import pytest

from execution.task_state import TaskStateStore


def test_task_records_are_independent_and_listed_newest_first(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("task-one", "planned", {"goal": "first", "plan": ["a"]})
    store.save("task-two", "executing", {"goal": "second", "plan": ["b"]})

    assert store.load("task-one")["state"]["payload"]["goal"] == "first"
    assert store.load("task-two")["state"]["payload"]["goal"] == "second"
    assert store.load()["state"]["task_id"] == "task-two"
    assert [item["task_id"] for item in store.list_tasks()] == ["task-two", "task-one"]


def test_task_state_payload_is_allow_listed(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("task-safe", "started", {
        "goal": "inspect", "api_key": "do-not-persist",
        "provider": {"secret": "do-not-persist"},
    })
    payload = store.load("task-safe")["state"]["payload"]
    assert payload == {"goal": "inspect"}
    raw = (store.records / "task-safe.json").read_text(encoding="utf-8")
    assert "do-not-persist" not in raw
    assert (store.records / "task-safe.json").stat().st_mode & 0o777 == 0o600


def test_task_id_rejects_path_traversal(tmp_path):
    store = TaskStateStore(tmp_path)
    with pytest.raises(ValueError):
        store.save("../escape", "started", {})
    with pytest.raises(ValueError):
        store.load("..")


def test_corrupt_record_is_reported_not_loaded(tmp_path):
    store = TaskStateStore(tmp_path)
    (store.records / "task-bad.json").write_text("{bad", encoding="utf-8")
    assert store.load("task-bad")["status"] == "corrupt"
    assert store.list_tasks()[0]["status"] == "corrupt"


def test_clear_task_only_removes_selected_record_and_active_pointer(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("task-one", "planned", {"goal": "one"})
    store.save("task-two", "planned", {"goal": "two"})
    store.clear("task-one")
    assert store.load("task-one")["status"] == "missing"
    assert store.load("task-two")["status"] == "stored"
    assert store.load()["state"]["task_id"] == "task-two"


def test_task_graph_metadata_survives_state_updates(tmp_path):
    store = TaskStateStore(tmp_path)
    graph = {"nodes": [{"id": "step_1", "description": "inspect",
        "dependencies": [], "status": "pending"}]}
    store.save("task-graph", "planned", {"goal": "goal", "task_graph": graph})
    saved = store.load("task-graph")["state"]["payload"]["task_graph"]
    assert saved == graph


def test_execution_cursor_persists_and_recovery_is_conservative(tmp_path):
    from execution.resume import ExecutionCursor
    store = TaskStateStore(tmp_path)
    cursor = ExecutionCursor()
    cursor.transition("executing", current_node_id="node-2", mutation_started=True)
    store.save("resume-check", "execution_started", {
        "goal": "update project", "execution_cursor": cursor.to_dict()
    })
    loaded = store.load("resume-check")
    assert loaded["status"] == "stored"
    assert loaded["state"]["version"] == 3
    decision = store.assess_recovery("resume-check")
    assert decision["action"] == "manual_reconciliation"
    assert decision["current_node_id"] == "node-2"


def test_task_state_store_reads_legacy_v1(tmp_path):
    import json
    store = TaskStateStore(tmp_path)
    path = store.records / "legacy-task.json"
    path.write_text(json.dumps({
        "version": 1, "task_id": "legacy-task", "workspace": str(store.workspace),
        "stage": "planned", "updated_at": "2026-01-01T00:00:00+00:00",
        "payload": {"goal": "old task"}
    }), encoding="utf-8")
    assert store.load("legacy-task")["status"] == "stored"


def test_workspace_fingerprint_detects_external_file_changes(tmp_path):
    store = TaskStateStore(tmp_path)
    (tmp_path / "app.py").write_text("print('before')", encoding="utf-8")
    store.save("fingerprint-task", "planned", {"goal": "change app"})
    state = store.load("fingerprint-task")["state"]
    assert store.compare_workspace_fingerprint(state)["action"] == "resume_allowed"
    (tmp_path / "app.py").write_text("print('external edit')", encoding="utf-8")
    result = store.compare_workspace_fingerprint(state)
    assert result["status"] == "drift"
    assert result["action"] == "manual_reconciliation"


def test_workspace_fingerprint_ignores_checkpoint_metadata(tmp_path):
    store = TaskStateStore(tmp_path)
    (tmp_path / "source.py").write_text("pass", encoding="utf-8")
    store.save("stable-task", "planned", {"goal": "inspect"})
    state = store.load("stable-task")["state"]
    (store.root / "unrelated-metadata.json").write_text("{}", encoding="utf-8")
    assert store.compare_workspace_fingerprint(state)["action"] == "resume_allowed"


def test_workspace_fingerprint_refuses_partial_snapshot_when_limits_exceeded(tmp_path, monkeypatch):
    store = TaskStateStore(tmp_path)
    (tmp_path / "one.py").write_text("pass", encoding="utf-8")
    monkeypatch.setattr(TaskStateStore, "FINGERPRINT_MAX_FILES", 0)
    assert store.workspace_fingerprint()["status"] == "unavailable"
    store.save("bounded-task", "planned", {"goal": "inspect"})
    state = store.load("bounded-task")["state"]
    assert store.compare_workspace_fingerprint(state)["action"] == "manual_reconciliation"


def test_node_verification_plan_and_evidence_survive_checkpoint(tmp_path):
    store = TaskStateStore(tmp_path)
    graph = {"nodes": [{
        "id": "step_1", "description": "verify", "dependencies": [],
        "status": "completed", "verification_commands": ["pytest -q"],
        "verification_evidence": {"status": "passed", "reason": "ok", "results": [{
            "command": "pytest -q", "status": "passed", "returncode": 0,
            "stdout": "3 passed", "stderr": ""}]}
    }]}
    store.save("verification-checkpoint", "node_boundary", {
        "goal": "goal", "task_graph": graph})
    saved = store.load("verification-checkpoint")["state"]["payload"]["task_graph"]["nodes"][0]
    assert saved["verification_commands"] == ["pytest -q"]
    assert saved["verification_evidence"]["results"][0]["returncode"] == 0
    assert saved["verification_evidence"]["results"][0]["stdout"] == "3 passed"


def test_assess_recovery_blocks_workspace_drift_before_advertising_resume(tmp_path):
    store = TaskStateStore(tmp_path)
    source = tmp_path / "app.py"
    source.write_text("stable", encoding="utf-8")
    store.save("drift-before-resume", "planned", {
        "goal": "continue safely",
        "task_graph": {"nodes": [{"id": "node-a", "dependencies": [], "status": "pending"}]},
    })
    source.write_text("external change", encoding="utf-8")
    decision = store.assess_recovery("drift-before-resume")
    assert decision["action"] == "manual_reconciliation"
    assert decision["status"] == "drift"
    assert "workspace changed" in decision["reason"]


def test_assess_recovery_blocks_unresolved_task_journal(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("journal-check", "planned", {
        "goal": "continue safely",
        "task_graph": {"nodes": [{"id": "node-a", "dependencies": [], "status": "pending"}]},
    })
    journal = store.workspace / ".zx_checkpoints" / "journals" / "journal-check-node-a"
    journal.mkdir(parents=True)
    (journal / "manifest.json").write_text(json.dumps({
        "version": 1, "owner_id": "journal-check-node-a", "workspace": str(store.workspace),
        "operations": [{"operation_id": "op-1", "status": "checkpointed"}],
        "tool_invocations": {}, "rollback_skips": [],
    }), encoding="utf-8")
    decision = store.assess_recovery("journal-check")
    assert decision["action"] == "manual_reconciliation"
    assert decision["status"] == "uncertain"


def test_assess_recovery_blocks_unreadable_journal_manifest(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("bad-journal", "planned", {
        "goal": "continue safely",
        "task_graph": {"nodes": [{"id": "node-a", "dependencies": [], "status": "pending"}]},
    })
    journal = store.workspace / ".zx_checkpoints" / "journals" / "bad-journal-node-a"
    journal.mkdir(parents=True)
    (journal / "manifest.json").write_text("{broken", encoding="utf-8")
    decision = store.assess_recovery("bad-journal")
    assert decision["action"] == "manual_reconciliation"
    assert decision["status"] == "uncertain"


def test_assess_recovery_allows_only_verified_durable_boundary(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("safe-boundary", "planned", {
        "goal": "continue safely",
        "task_graph": {"nodes": [{"id": "node-a", "dependencies": [], "status": "pending"}]},
    })
    decision = store.assess_recovery("safe-boundary")
    assert decision["action"] == "restart_from_user_confirmation"
    assert decision["workspace"]["status"] == "match"
    assert decision["journal"]["status"] == "clear"


def test_assess_recovery_blocks_failed_tool_invocation_as_uncertain(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("failed-call", "planned", {
        "goal": "continue safely",
        "task_graph": {"nodes": [{"id": "node-a", "dependencies": [], "status": "pending"}]},
    })
    journal = store.workspace / ".zx_checkpoints" / "journals" / "failed-call-node-a"
    journal.mkdir(parents=True)
    (journal / "manifest.json").write_text(json.dumps({
        "version": 1, "owner_id": "failed-call-node-a", "workspace": str(store.workspace),
        "operations": [], "tool_invocations": {"call": {"status": "failed"}},
        "rollback_skips": [],
    }), encoding="utf-8")
    decision = store.assess_recovery("failed-call")
    assert decision["action"] == "manual_reconciliation"
    assert decision["status"] == "uncertain"


def test_assess_recovery_blocks_journal_rollback_skips(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("rollback-skips", "planned", {
        "goal": "continue safely",
        "task_graph": {"nodes": [{"id": "node-a", "dependencies": [], "status": "pending"}]},
    })
    journal = store.workspace / ".zx_checkpoints" / "journals" / "rollback-skips-node-a"
    journal.mkdir(parents=True)
    (journal / "manifest.json").write_text(json.dumps({
        "version": 1, "owner_id": "rollback-skips-node-a", "workspace": str(store.workspace),
        "operations": [], "tool_invocations": {}, "rollback_skips": ["external edit preserved"],
    }), encoding="utf-8")
    decision = store.assess_recovery("rollback-skips")
    assert decision["action"] == "manual_reconciliation"
    assert decision["journal"]["rollback_skips"] == 1
