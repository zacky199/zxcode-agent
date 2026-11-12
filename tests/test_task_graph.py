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
