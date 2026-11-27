import pytest

from planner.task_graph import TaskGraph, TaskGraphError, TaskNode
from planner.planner import TaskPlanner


def graph_with(nodes):
    graph = TaskGraph()
    for node in nodes:
        graph.add_node(node)
    return graph


def test_topological_order_respects_forward_dependencies():
    graph = graph_with([
        TaskNode("step_1", "final", ["step_3"]),
        TaskNode("step_2", "independent"),
        TaskNode("step_3", "foundation", ["step_2"]),
    ])
    graph.validate()
    order = graph.topological_order()
    assert order.index("step_2") < order.index("step_3") < order.index("step_1")


def test_ready_nodes_only_return_nodes_with_completed_prerequisites():
    graph = graph_with([
        TaskNode("a", "A"),
        TaskNode("b", "B", ["a"]),
        TaskNode("c", "C"),
    ])
    assert [node.id for node in graph.ready_nodes()] == ["a", "c"]
    graph.update_status("a", "completed")
    assert [node.id for node in graph.ready_nodes()] == ["b", "c"]


def test_cycle_is_rejected():
    graph = graph_with([TaskNode("a", "A", ["b"]), TaskNode("b", "B", ["a"])])
    with pytest.raises(TaskGraphError, match="cycle"):
        graph.validate()


def test_unknown_and_self_dependencies_are_rejected():
    with pytest.raises(TaskGraphError, match="unknown dependency"):
        graph_with([TaskNode("a", "A", ["missing"])]).validate()
    with pytest.raises(TaskGraphError, match="itself"):
        graph_with([TaskNode("a", "A", ["a"])]).validate()


def test_failed_dependency_blocks_all_descendants():
    graph = graph_with([
        TaskNode("a", "A"), TaskNode("b", "B", ["a"]),
        TaskNode("c", "C", ["b"]), TaskNode("d", "D"),
    ])
    graph.update_status("a", "failed")
    assert set(graph.mark_blocked_dependents()) == {"b", "c"}
    assert graph.nodes["d"].status == "pending"


def test_planner_preserves_forward_dependency_indices():
    async def callback(system, prompt):
        return '{"steps":[' \
            '{"description":"finish","depends_on":[2]},' \
            '{"description":"parallel","depends_on":[]},' \
            '{"description":"prepare","depends_on":[1]}]}'
    graph = __import__("asyncio").run(TaskPlanner(callback).create_plan("goal"))
    assert not graph.fallback_used
    assert graph.topological_order() == ["step_2", "step_3", "step_1"]


def test_planner_falls_back_on_cycle_instead_of_silently_dropping_edges():
    async def callback(system, prompt):
        return '{"steps":[' \
            '{"description":"A","depends_on":[1]},' \
            '{"description":"B","depends_on":[0]}]}'
    graph = __import__("asyncio").run(TaskPlanner(callback).create_plan("original goal"))
    assert graph.fallback_used
    assert list(graph.nodes) == ["step_1"]
    assert graph.nodes["step_1"].description == "original goal"


def test_planner_rejects_oversized_single_step_description():
    import json
    async def callback(system, prompt):
        return json.dumps({"steps": [{"description": "x" * 2001, "depends_on": []}]})
    graph = __import__("asyncio").run(TaskPlanner(callback).create_plan("original goal"))
    assert graph.fallback_used
    assert graph.nodes["step_1"].description == "original goal"


def test_planner_rejects_plan_with_oversized_aggregate_descriptions():
    import json
    async def callback(system, prompt):
        return json.dumps({"steps": [
            {"description": "x" * 2000, "depends_on": []} for _ in range(6)
        ]})
    graph = __import__("asyncio").run(TaskPlanner(callback).create_plan("original goal"))
    assert graph.fallback_used
    assert graph.nodes["step_1"].description == "original goal"


def test_planner_accepts_descriptions_at_the_character_limits():
    import json
    async def callback(system, prompt):
        return json.dumps({"steps": [
            {"description": "x" * 2000, "depends_on": []} for _ in range(5)
        ]})
    graph = __import__("asyncio").run(TaskPlanner(callback).create_plan("goal"))
    assert not graph.fallback_used
    assert len(graph.nodes) == 5


def test_failed_node_replan_replaces_node_and_preserves_prerequisites_and_dependents():
    graph = graph_with([
        TaskNode("base", "foundation"),
        TaskNode("broken", "failed implementation", ["base"], status="failed", replan_count=0),
        TaskNode("next", "continue after repair", ["broken"]),
        TaskNode("other", "unrelated independent work"),
    ])
    created = graph.replace_node_with_subtasks("broken", ["inspect root cause", "implement smaller fix", "verify result"])
    assert created == ["broken_r1_1", "broken_r1_2", "broken_r1_3"]
    assert graph.nodes[created[0]].dependencies == ["base"]
    assert graph.nodes[created[1]].dependencies == [created[0]]
    assert graph.nodes[created[2]].dependencies == [created[1]]
    assert graph.nodes["next"].dependencies == [created[-1]]
    assert graph.nodes["other"].dependencies == []
    graph.validate()


def test_replan_graph_replacement_rejects_invalid_subtask_list_atomically():
    graph = graph_with([TaskNode("failed", "failed node", status="failed"), TaskNode("desc", "dependent", ["failed"])])
    before = TaskPlanner.to_dict(graph)
    with pytest.raises(TaskGraphError):
        graph.replace_node_with_subtasks("failed", ["only one subtask"])
    assert TaskPlanner.to_dict(graph) == before
