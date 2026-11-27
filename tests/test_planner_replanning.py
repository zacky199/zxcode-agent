import asyncio
import json
from planner.planner import TaskPlanner


def test_failed_node_replan_returns_bounded_subtasks_and_excludes_commands():
    prompts = []
    async def callback(system, prompt):
        prompts.append((system, prompt))
        return json.dumps({"steps": ["inspect failed component", "implement alternative", "verify fix"]})
    planner = TaskPlanner(callback)
    result = asyncio.run(planner.replan_failed_node(
        goal="implement a feature", node_description="original task", evidence="test failure"))
    assert result == ["inspect failed component", "implement alternative", "verify fix"]
    assert "no shell commands" in prompts[0][0]
    assert "untrusted_evidence" in prompts[0][1]


def test_failed_node_replan_rejects_malformed_or_oversized_output():
    async def malformed(system, prompt):
        return "not JSON"
    async def too_long(system, prompt):
        return json.dumps({"steps": ["a" * 2001, "subtask two"]})
    assert asyncio.run(TaskPlanner(malformed).replan_failed_node(
        goal="goal", node_description="failed", evidence="evidence")) is None
    assert asyncio.run(TaskPlanner(too_long).replan_failed_node(
        goal="goal", node_description="failed", evidence="evidence")) is None
