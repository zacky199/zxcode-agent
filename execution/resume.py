"""Crash-recovery metadata and conservative resume decisions.

This module deliberately never replays an in-flight tool call. A workspace may
have been mutated immediately before a process crash but before its result was
checkpointed; that state requires reconciliation, not automatic replay.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class ExecutionCursor:
    phase: str = "created"
    current_node_id: str = ""
    completed_node_ids: List[str] = field(default_factory=list)
    interrupted_node_id: str = ""
    mutation_started: bool = False
    sequence: int = 0

    VALID_PHASES = {
        "created", "planning", "planned", "executing", "verification",
        "review", "completed", "blocked", "failed",
    }

    def transition(self, phase: str, *, current_node_id: str = "",
                   mutation_started: bool | None = None) -> None:
        if phase not in self.VALID_PHASES:
            raise ValueError(f"invalid execution phase: {phase}")
        self.phase = phase
        self.current_node_id = str(current_node_id)[:80]
        if mutation_started is not None:
            self.mutation_started = bool(mutation_started)
        self.sequence += 1

    def complete_node(self, node_id: str) -> None:
        node_id = str(node_id)[:80]
        if node_id and node_id not in self.completed_node_ids:
            self.completed_node_ids.append(node_id)
        if self.current_node_id == node_id:
            self.current_node_id = ""
        self.mutation_started = False
        self.sequence += 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "ExecutionCursor":
        if not isinstance(value, dict):
            raise ValueError("execution cursor must be an object")
        phase = value.get("phase", "created")
        completed = value.get("completed_node_ids", [])
        if not isinstance(phase, str) or phase not in cls.VALID_PHASES or not isinstance(completed, list):
            raise ValueError("invalid execution cursor")
        if len(completed) > 100 or any(not isinstance(x, str) or len(x) > 80 for x in completed):
            raise ValueError("invalid completed node list")
        sequence = value.get("sequence", 0)
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("invalid cursor sequence")
        return cls(
            phase=phase,
            current_node_id=str(value.get("current_node_id", ""))[:80],
            completed_node_ids=list(dict.fromkeys(completed)),
            interrupted_node_id=str(value.get("interrupted_node_id", ""))[:80],
            mutation_started=bool(value.get("mutation_started", False)),
            sequence=sequence,
        )


def assess_recovery(state: Dict[str, Any]) -> dict:
    """Return an advisory recovery decision; never performs execution."""
    if not isinstance(state, dict) or not isinstance(state.get("payload"), dict):
        return {"status": "invalid", "action": "do_not_resume", "reason": "task state is malformed"}
    stage = str(state.get("stage", "unknown"))
    payload = state["payload"]
    raw_cursor = payload.get("execution_cursor")
    try:
        cursor = ExecutionCursor.from_dict(raw_cursor) if raw_cursor is not None else ExecutionCursor()
    except ValueError as exc:
        return {"status": "invalid", "action": "do_not_resume", "reason": str(exc)}

    if stage in {"completed", "blocked", "failed"} or cursor.phase in {"completed", "blocked", "failed"}:
        return {"status": "terminal", "action": "inspect_result", "phase": cursor.phase,
                "reason": "task has already reached a terminal state"}
    graph = payload.get("task_graph", {})
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    if not isinstance(nodes, list) or len(nodes) > 100:
        return {"status": "invalid", "action": "do_not_resume", "reason": "task graph is malformed or exceeds limits"}
    node_ids = []
    running = []
    allowed_statuses = {"pending", "ready", "running", "completed", "failed", "blocked"}
    for node in nodes:
        if not isinstance(node, dict):
            return {"status": "invalid", "action": "do_not_resume", "reason": "task graph contains a malformed node"}
        node_id = node.get("id")
        dependencies = node.get("dependencies", [])
        status_value = node.get("status", "pending")
        if (not isinstance(node_id, str) or not node_id or len(node_id) > 80
                or not isinstance(dependencies, list) or len(dependencies) > 100
                or any(not isinstance(dep, str) for dep in dependencies)
                or not isinstance(status_value, str) or status_value not in allowed_statuses):
            return {"status": "invalid", "action": "do_not_resume", "reason": "task graph node metadata is invalid"}
        if node_id in node_ids:
            return {"status": "invalid", "action": "do_not_resume", "reason": "task graph contains duplicate node IDs"}
        node_ids.append(node_id)
        if status_value == "running":
            running.append(node_id)
    node_set = set(node_ids)
    if any(dep not in node_set for node in nodes for dep in node.get("dependencies", [])):
        return {"status": "invalid", "action": "do_not_resume", "reason": "task graph references an unknown dependency"}
    if any(node_id not in node_set for node_id in cursor.completed_node_ids):
        return {"status": "invalid", "action": "do_not_resume", "reason": "execution cursor references an unknown completed node"}
    if running or cursor.mutation_started or cursor.phase == "executing":
        return {"status": "interrupted", "action": "manual_reconciliation",
                "phase": cursor.phase, "current_node_id": running[0] if running else cursor.current_node_id,
                "reason": "a node may have performed mutations after its last durable checkpoint; automatic replay is unsafe"}
    if cursor.phase in {"verification", "review"}:
        return {"status": "interrupted", "action": "manual_reconciliation",
                "phase": cursor.phase,
                "reason": "verification/review phase was interrupted; inspect existing changes before continuing"}
    return {"status": "interrupted", "action": "restart_from_user_confirmation",
            "phase": cursor.phase,
            "reason": "the graph is at a durable node boundary; explicit user confirmation is required to continue pending nodes"}
