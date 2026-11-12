from typing import Any, Dict, List

from .task_graph import TaskGraph, TaskNode, TaskGraphError
from utils.validation import ValidationUtils


class TaskPlanner:
    """Build and validate a dependency-aware TaskGraph from an LLM response."""

    FALLBACK_WARNING = (
        "Warning: Planner response was invalid or not readable; continuing with the "
        "original request as a single step, without decomposition."
    )

    def __init__(self, llm_callback: Any):
        self.llm_callback = llm_callback

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a meticulous software project planner.\n"
            "Break the user request into a logical sequence of actionable steps.\n"
            "Return ONLY a JSON object (no markdown, no commentary) of this exact shape:\n"
            '{"objective": "<goal in one line>", "steps": '
            '[{"description": "<step>", "depends_on": [<indices of steps this depends on, 0-based>], "verification_commands": [<optional commands>]}]}\n'
            "Rules:\n- 3 to 8 steps max.\n"
            "- Each description is concrete and self-contained.\n"
            "- Dependencies may reference any step index, but not the step itself.\n"
            "- Independent steps use an empty dependency list.\n"
            "- Include every real dependency; do not invent dependencies."
        )

    def _fallback(self, user_prompt: str) -> TaskGraph:
        graph = TaskGraph()
        graph.fallback_used = True
        graph.fallback_warning = self.FALLBACK_WARNING
        graph.add_node(TaskNode(id="step_1", description=user_prompt))
        return graph

    async def create_plan(self, user_prompt: str, context: str = "") -> TaskGraph:
        prompt = f"Workspace Context:\n{context[:6000]}\n\nUser Request: {user_prompt}"
        try:
            response = await self.llm_callback(self._system_prompt(), prompt)
            is_valid, data = ValidationUtils.parse_json_safely(response)
            if not is_valid or not isinstance(data, dict):
                return self._fallback(user_prompt)
            steps = data.get("steps")
            if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
                return self._fallback(user_prompt)

            graph = TaskGraph()
            raw_dependencies = []
            for idx, raw_step in enumerate(steps):
                if not isinstance(raw_step, dict):
                    return self._fallback(user_prompt)
                description = raw_step.get("description")
                if not isinstance(description, str) or not description.strip():
                    return self._fallback(user_prompt)
                node_id = f"step_{idx + 1}"
                verification_commands = raw_step.get("verification_commands", [])
                if (not isinstance(verification_commands, list) or len(verification_commands) > 5
                        or any(not isinstance(command, str) or not command.strip() or len(command) > 500
                               for command in verification_commands)):
                    return self._fallback(user_prompt)
                graph.add_node(TaskNode(id=node_id, description=description.strip(),
                                        verification_commands=[x.strip() for x in verification_commands]))
                raw_dependencies.append(raw_step.get("depends_on", []))

            # Resolve only after all nodes exist so forward dependencies are preserved.
            for idx, deps_raw in enumerate(raw_dependencies):
                if not isinstance(deps_raw, list):
                    return self._fallback(user_prompt)
                deps = []
                for dep in deps_raw:
                    if isinstance(dep, bool) or not isinstance(dep, int):
                        return self._fallback(user_prompt)
                    if dep < 0 or dep >= len(steps) or dep == idx:
                        return self._fallback(user_prompt)
                    dep_id = f"step_{dep + 1}"
                    if dep_id in deps:
                        return self._fallback(user_prompt)
                    deps.append(dep_id)
                graph.nodes[f"step_{idx + 1}"].dependencies = deps
            graph.validate()
            return graph
        except (Exception, TaskGraphError):
            return self._fallback(user_prompt)

    @staticmethod
    def to_dict(graph: TaskGraph) -> Dict[str, Any]:
        graph.validate()
        return {"nodes": [{"id": n.id, "description": n.description,
            "dependencies": list(n.dependencies), "status": n.status,
            "result": n.result[:6000], "error_log": n.error_log[:3000],
            "retry_count": n.retry_count, "max_retries": n.max_retries,
            "acceptance": dict(n.acceptance),
            "verification_commands": list(n.verification_commands),
            "verification_evidence": dict(n.verification_evidence)}
            for n in graph.nodes.values()]}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> TaskGraph:
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            raise TaskGraphError("invalid serialized task graph")
        graph = TaskGraph()
        for node in data["nodes"]:
            if not isinstance(node, dict):
                raise TaskGraphError("invalid serialized task node")
            graph.add_node(TaskNode(id=node["id"], description=node["description"],
                dependencies=node.get("dependencies", []), status=node.get("status", "pending"),
                result=str(node.get("result", ""))[:6000],
                error_log=str(node.get("error_log", ""))[:3000],
                retry_count=max(0, min(int(node.get("retry_count", 0)), 10)),
                max_retries=max(0, min(int(node.get("max_retries", 3)), 10)),
                acceptance=node.get("acceptance", {}) if isinstance(node.get("acceptance", {}), dict) else {},
                verification_commands=node.get("verification_commands", []) if isinstance(node.get("verification_commands", []), list) else [],
                verification_evidence=node.get("verification_evidence", {}) if isinstance(node.get("verification_evidence", {}), dict) else {}))
        graph.validate()
        return graph
