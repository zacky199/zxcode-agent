from execution.resume import ExecutionCursor, assess_recovery


def _state(stage, cursor):
    return {"stage": stage, "payload": {"execution_cursor": cursor.to_dict()}}


def test_cursor_round_trip_and_sequence():
    cursor = ExecutionCursor()
    cursor.transition("planning")
    cursor.transition("planned")
    cursor.complete_node("inspect")
    restored = ExecutionCursor.from_dict(cursor.to_dict())
    assert restored.phase == "planned"
    assert restored.completed_node_ids == ["inspect"]
    assert restored.sequence == 3


def test_executing_crash_requires_manual_reconciliation():
    cursor = ExecutionCursor()
    cursor.transition("executing", current_node_id="edit-source", mutation_started=True)
    result = assess_recovery(_state("execution_started", cursor))
    assert result["status"] == "interrupted"
    assert result["action"] == "manual_reconciliation"
    assert result["current_node_id"] == "edit-source"


def test_pre_execution_crash_does_not_claim_auto_resume():
    cursor = ExecutionCursor()
    cursor.transition("planned")
    result = assess_recovery(_state("planned", cursor))
    assert result["action"] == "restart_from_user_confirmation"


def test_terminal_task_is_not_resumed():
    cursor = ExecutionCursor()
    cursor.transition("completed")
    result = assess_recovery(_state("completed", cursor))
    assert result["status"] == "terminal"
    assert result["action"] == "inspect_result"


def test_invalid_cursor_is_rejected():
    try:
        ExecutionCursor.from_dict({"phase": "teleport", "completed_node_ids": []})
    except ValueError:
        pass
    else:
        raise AssertionError("invalid phase accepted")


def test_graph_rejects_duplicate_node_ids():
    state = {"stage": "planned", "payload": {"task_graph": {"nodes": [
        {"id": "same", "dependencies": [], "status": "pending"},
        {"id": "same", "dependencies": [], "status": "pending"},
    ]}}}
    result = assess_recovery(state)
    assert result["status"] == "invalid"
    assert result["action"] == "do_not_resume"


def test_graph_rejects_unknown_dependency():
    state = {"stage": "planned", "payload": {"task_graph": {"nodes": [
        {"id": "node-a", "dependencies": ["missing"], "status": "pending"},
    ]}}}
    result = assess_recovery(state)
    assert result["status"] == "invalid"
    assert "unknown dependency" in result["reason"]


def test_cursor_rejects_completed_node_missing_from_graph():
    cursor = ExecutionCursor(completed_node_ids=["not-in-graph"])
    state = {"stage": "planned", "payload": {
        "execution_cursor": cursor.to_dict(),
        "task_graph": {"nodes": [{"id": "node-a", "dependencies": [], "status": "pending"}]},
    }}
    result = assess_recovery(state)
    assert result["status"] == "invalid"
