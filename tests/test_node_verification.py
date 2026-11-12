from execution.acceptance import NodeAcceptanceContract
from execution.verification import NodeVerificationPlan
from planner.planner import TaskPlanner
from planner.task_graph import TaskGraph, TaskNode


class FakeSandbox:
    def __init__(self, code=0):
        self.code = code
        self.calls = []

    def run_command(self, command, timeout=None):
        self.calls.append((command, timeout))
        return self.code, "stdout evidence", "stderr evidence"


def test_verification_plan_executes_and_captures_exit_code_and_output():
    sandbox = FakeSandbox(0)
    result = NodeVerificationPlan.execute(sandbox, ["python -m compileall -q ."])
    assert result["status"] == "passed"
    assert result["results"][0]["returncode"] == 0
    assert result["results"][0]["stdout"] == "stdout evidence"
    assert sandbox.calls[0][1] == 120


def test_failed_verification_blocks_node_acceptance():
    result = NodeVerificationPlan.execute(FakeSandbox(2), ["pytest -q"])
    assert result["status"] == "failed"
    accepted = NodeAcceptanceContract.evaluate(
        result="implementation done", fatal=False, journal_closed=True,
        verification=result)
    assert not accepted.accepted
    assert not accepted.checks["node_verification_passed"]


def test_unconfigured_verification_is_explicit_but_not_a_false_failure():
    result = NodeVerificationPlan.execute(FakeSandbox(), [])
    assert result["status"] == "not_configured"
    accepted = NodeAcceptanceContract.evaluate(
        result="done", fatal=False, journal_closed=True, verification=result)
    assert accepted.accepted


def test_verification_plan_rejects_unbounded_or_malformed_commands():
    for commands in ([""], ["x" * 501], ["x"] * 6, "pytest -q"):
        try:
            NodeVerificationPlan.normalize(commands)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid plan accepted: {commands!r}")


def test_verification_plan_roundtrips_with_task_graph():
    graph = TaskGraph()
    graph.add_node(TaskNode("step_1", "run tests",
                            verification_commands=["pytest -q"],
                            verification_evidence={"status": "passed", "results": []}))
    data = TaskPlanner.to_dict(graph)
    restored = TaskPlanner.from_dict(data)
    assert restored.nodes["step_1"].verification_commands == ["pytest -q"]
    assert restored.nodes["step_1"].verification_evidence["status"] == "passed"


def test_planner_accepts_bounded_node_verification_commands():
    import asyncio

    async def callback(system, prompt):
        return '{"objective":"verify","steps":[{"description":"run tests",' \
               '"depends_on":[],"verification_commands":["pytest -q"]}]}'

    graph = asyncio.run(TaskPlanner(callback).create_plan("verify project"))
    assert graph.nodes["step_1"].verification_commands == ["pytest -q"]


def test_planner_rejects_oversized_node_verification_command():
    import asyncio

    async def callback(system, prompt):
        import json
        return json.dumps({"objective": "verify", "steps": [{
            "description": "run tests", "depends_on": [],
            "verification_commands": ["x" * 501]}]})

    graph = asyncio.run(TaskPlanner(callback).create_plan("verify project"))
    assert graph.fallback_used
    assert graph.nodes["step_1"].verification_commands == []
