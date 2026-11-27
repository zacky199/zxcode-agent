"""Validated dependency graph primitives for deterministic task planning."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class TaskNode:
    id: str
    description: str
    dependencies: List[str] = field(default_factory=list)
    status: str = "pending"  # pending, ready, running, completed, failed, blocked
    result: str = ""
    error_log: str = ""
    retry_count: int = 0
    max_retries: int = 3
    replan_count: int = 0
    acceptance: dict = field(default_factory=dict)
    verification_commands: List[str] = field(default_factory=list)
    verification_evidence: dict = field(default_factory=dict)


class TaskGraphError(ValueError):
    """Raised when a task graph is malformed or cannot be scheduled."""


class TaskGraph:
    """A small DAG with validation and deterministic scheduling helpers."""

    VALID_STATUSES = {"pending", "ready", "running", "completed", "failed", "blocked"}

    def __init__(self):
        self.nodes: Dict[str, TaskNode] = {}
        self.fallback_used: bool = False
        self.fallback_warning: str = ""

    def add_node(self, node: TaskNode) -> None:
        if not node.id or node.id in self.nodes:
            raise TaskGraphError(f"duplicate or empty task node id: {node.id!r}")
        if not isinstance(node.description, str) or not node.description.strip():
            raise TaskGraphError(f"task node {node.id!r} has an empty description")
        self.nodes[node.id] = node

    def validate(self) -> None:
        if not self.nodes:
            raise TaskGraphError("task graph must contain at least one node")
        for node in self.nodes.values():
            if node.status not in self.VALID_STATUSES:
                raise TaskGraphError(f"invalid status for {node.id}: {node.status}")
            if (not isinstance(node.verification_commands, list)
                    or len(node.verification_commands) > 5
                    or any(not isinstance(command, str) or not command.strip() or len(command) > 500
                           for command in node.verification_commands)):
                raise TaskGraphError(f"invalid verification commands for {node.id}")
            if not isinstance(node.verification_evidence, dict):
                raise TaskGraphError(f"invalid verification evidence for {node.id}")
            if len(set(node.dependencies)) != len(node.dependencies):
                raise TaskGraphError(f"duplicate dependencies for {node.id}")
            for dependency in node.dependencies:
                if dependency == node.id:
                    raise TaskGraphError(f"task node {node.id} cannot depend on itself")
                if dependency not in self.nodes:
                    raise TaskGraphError(f"unknown dependency {dependency!r} for {node.id}")
        self.topological_order()  # detects cycles

    def topological_order(self) -> List[str]:
        indegree = {node_id: len(node.dependencies) for node_id, node in self.nodes.items()}
        dependents = {node_id: [] for node_id in self.nodes}
        for node_id, node in self.nodes.items():
            for dependency in node.dependencies:
                if dependency not in self.nodes:
                    raise TaskGraphError(f"unknown dependency {dependency!r} for {node_id}")
                dependents[dependency].append(node_id)
        ready = [node_id for node_id in self.nodes if indegree[node_id] == 0]
        order = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for child in dependents[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if len(order) != len(self.nodes):
            raise TaskGraphError("task graph contains a dependency cycle")
        return order

    def ready_nodes(self) -> List[TaskNode]:
        """Return pending nodes whose dependencies have all completed."""
        self.validate()
        return [node for node in self.nodes.values()
                if node.status == "pending"
                and all(self.nodes[dep].status == "completed" for dep in node.dependencies)]

    def mark_blocked_dependents(self) -> List[str]:
        """Mark pending descendants of failed/blocked nodes as blocked."""
        self.validate()
        blocked = []
        changed = True
        while changed:
            changed = False
            for node in self.nodes.values():
                if node.status != "pending":
                    continue
                if any(self.nodes[dep].status in {"failed", "blocked"} for dep in node.dependencies):
                    node.status = "blocked"
                    blocked.append(node.id)
                    changed = True
        return blocked

    def update_status(self, node_id: str, status: str, result: str = "", error_log: str = "", acceptance: dict = None, verification_evidence: dict = None) -> None:
        if status not in self.VALID_STATUSES:
            raise TaskGraphError(f"invalid task status: {status}")
        if node_id not in self.nodes:
            raise TaskGraphError(f"unknown task node: {node_id}")
        self.nodes[node_id].status = status
        self.nodes[node_id].result = result
        if error_log:
            self.nodes[node_id].error_log = error_log
        if acceptance is not None:
            self.nodes[node_id].acceptance = dict(acceptance)
        if verification_evidence is not None:
            self.nodes[node_id].verification_evidence = dict(verification_evidence)

    def replace_node_with_subtasks(self, node_id: str, descriptions: List[str],
                                  *, max_subtasks: int = 3) -> List[str]:
        """Replace one failed node with a bounded sequential sub-DAG, preserving its edges.

        Descendants depend on the final replacement node; prerequisites are inherited
        by the first replacement node. The operation is validated before committing.
        """
        if node_id not in self.nodes:
            raise TaskGraphError(f"unknown node to replan: {node_id}")
        original = self.nodes[node_id]
        if original.status not in {"failed", "pending", "running"}:
            raise TaskGraphError("only failed, pending, or running nodes may be replanned")
        if not isinstance(descriptions, list) or not 2 <= len(descriptions) <= max_subtasks:
            raise TaskGraphError(f"replan must contain 2 to {max_subtasks} subtasks")
        if any(not isinstance(d, str) or not d.strip() or len(d) > 2000 for d in descriptions):
            raise TaskGraphError("replan contains invalid or oversized subtask descriptions")
        suffix = max(1, original.replan_count + 1)
        new_ids = [f"{node_id}_r{suffix}_{i + 1}" for i in range(len(descriptions))]
        if any(new_id in self.nodes for new_id in new_ids):
            raise TaskGraphError("replan generated colliding node IDs")
        candidate = dict(self.nodes)
        del candidate[node_id]
        new_nodes = []
        for i, (new_id, description) in enumerate(zip(new_ids, descriptions)):
            deps = list(original.dependencies) if i == 0 else [new_ids[i - 1]]
            new_nodes.append(TaskNode(id=new_id, description=description.strip(), dependencies=deps,
                                   max_retries=1, replan_count=suffix))
        for existing in candidate.values():
            existing.dependencies = [new_ids[-1] if dep == node_id else dep
                                    for dep in existing.dependencies]
        for replacement in new_nodes:
            candidate[replacement.id] = replacement
        candidate_graph = TaskGraph()
        candidate_graph.nodes = candidate
        candidate_graph.validate()
        self.nodes = candidate
        return new_ids

    def is_completed(self) -> bool:
        return bool(self.nodes) and all(n.status == "completed" for n in self.nodes.values())

    def has_fatal_failure(self) -> bool:
        return any(n.status == "failed" and n.retry_count >= n.max_retries for n in self.nodes.values())
