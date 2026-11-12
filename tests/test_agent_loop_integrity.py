"""Regression tests for planner routing and safe agent-loop compaction."""
from pathlib import Path

import pytest

from core import AgentConfig, ZXAgentPro
from providers.multi_ai import RoleRouter


class Response:
    def __init__(self, text):
        self.text = text
        self.tool_calls = []
        self.raw = {}


class RoutedProvider:
    supports_tools = True

    def __init__(self, name):
        self.model = name
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete_async(self, system, messages, tools=None):
        self.complete_calls += 1
        return Response(
            '{"objective":"routed task","steps":'
            '[{"description":"step 1","depends_on":[]}]}'
        )

    async def stream_complete_async(self, system, messages, tools=None):
        self.stream_calls += 1
        yield {"type": "text", "content": "finished"}
        yield {"type": "done", "content": ""}


@pytest.mark.asyncio
async def test_planner_and_execution_use_separate_role_providers(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    main = RoutedProvider("main")
    planner = RoutedProvider("planner")
    agent = ZXAgentPro(
        main,
        workspace,
        role_router=RoleRouter({"planner": planner}),
        config=AgentConfig(store_tool_memory=False),
    )

    events = [event async for event in agent.chat_stream("implement a feature")]

    assert events[-1]["type"] == "done"
    assert planner.complete_calls == 1
    assert planner.stream_calls == 0
    assert main.complete_calls == 0
    assert main.stream_calls == 1


@pytest.mark.asyncio
async def test_planner_provider_falls_back_to_main_when_role_is_missing(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    main = RoutedProvider("main")
    agent = ZXAgentPro(
        main,
        workspace,
        role_router=RoleRouter({"coder": RoutedProvider("coder")}),
    )

    await agent.planner.create_plan("small task")

    assert main.complete_calls == 1


@pytest.mark.asyncio
async def test_loop_guard_does_not_accumulate_duplicates_across_progress(
    tmp_path,
):
    class SequenceProvider(RoutedProvider):
        def __init__(self):
            super().__init__("sequence")
            self.stream_calls = 0

        async def stream_complete_async(self, system, messages, tools=None):
            self.stream_calls += 1
            calls = [
                ("write_file", {"file_path": "a.txt", "content": "same"}),
                ("write_file", {"file_path": "b.txt", "content": "other"}),
                ("write_file", {"file_path": "a.txt", "content": "same"}),
            ]
            if self.stream_calls <= len(calls):
                name, arguments = calls[self.stream_calls - 1]
                yield {"type": "tool_call", "content": {
                    "id": f"call-{self.stream_calls}",
                    "name": name,
                    "arguments": arguments,
                }}
                yield {"type": "done", "content": ""}
            else:
                yield {"type": "text", "content": "finished"}
                yield {"type": "done", "content": ""}

    workspace = tmp_path / "project"
    workspace.mkdir()
    provider = SequenceProvider()
    agent = ZXAgentPro(
        provider,
        workspace,
        config=AgentConfig(
            max_repeated_tool_calls=2,
            max_iterations=8,
            store_tool_memory=False,
        ),
    )

    events = [event async for event in agent.chat_stream("write files")]

    statuses = [event["content"] for event in events if event["type"] == "status"]
    assert events[-1]["type"] == "done"
    assert not any("repeated" in str(status).lower() for status in statuses)
    assert not (workspace / "a.txt").exists()
    assert not (workspace / "b.txt").exists()
    assert sum(event["type"] == "tool_result"
               and event["content"].get("status") == "error"
               for event in events) == 3
    assert provider.stream_calls == 4


def _agent_for_compaction(workspace: Path) -> ZXAgentPro:
    provider = RoutedProvider("compaction")
    return ZXAgentPro(
        provider,
        workspace,
        config=AgentConfig(max_context_tokens=32, compaction_threshold=0.5),
    )


def test_compaction_preserves_latest_user_and_complete_tool_call_turn(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    agent = _agent_for_compaction(workspace)
    tool_call = {
        "id": "call-7",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"file_path":"x"}'},
    }
    messages = [
        {"role": "user", "content": "old request " + "old " * 100},
        {"role": "assistant", "content": "old answer " + "old " * 100},
        {"role": "user", "content": "LATEST REQUEST " + "keep-me " * 100},
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        {"role": "tool", "tool_call_id": "call-7", "content": "tool result"},
        {"role": "assistant", "content": "response " + "tail " * 100},
        {"role": "assistant", "content": "continuation " + "tail " * 100},
        {"role": "assistant", "content": "continuation " + "tail " * 100},
        {"role": "assistant", "content": "continuation " + "tail " * 100},
        {"role": "assistant", "content": "continuation " + "tail " * 100},
        {"role": "assistant", "content": "continuation " + "tail " * 100},
    ]

    compacted = agent._compact_messages(messages, "system")

    assert any(
        msg.get("role") == "user" and "LATEST REQUEST" in msg.get("content", "")
        for msg in compacted
    )
    call_ids = {
        call["id"]
        for msg in compacted
        if msg.get("role") == "assistant"
        for call in msg.get("tool_calls", [])
    }
    result_ids = {
        msg.get("tool_call_id")
        for msg in compacted
        if msg.get("role") == "tool"
    }
    assert call_ids == {"call-7"}
    assert result_ids == call_ids
    assert compacted.index(next(msg for msg in compacted if msg.get("role") == "user" and "LATEST REQUEST" in msg.get("content", ""))) < compacted.index(next(msg for msg in compacted if msg.get("role") == "assistant" and msg.get("tool_calls")))


@pytest.mark.asyncio
async def test_agent_uses_dependency_safe_order_and_persists_task_graph(tmp_path):
    class DependencyProvider(RoutedProvider):
        def __init__(self):
            super().__init__("dependency-provider")
            self.execution_messages = None

        async def complete_async(self, system, messages, tools=None):
            self.complete_calls += 1
            return Response(
                '{"objective":"dependency test","steps":['
                '{"description":"finish","depends_on":[2]},'
                '{"description":"independent","depends_on":[]},'
                '{"description":"prepare","depends_on":[1]}]}'
            )

        async def stream_complete_async(self, system, messages, tools=None):
            self.stream_calls += 1
            self.execution_messages = [dict(message) for message in messages]
            yield {"type": "text", "content": "completed"}
            yield {"type": "done", "content": ""}

    workspace = tmp_path / "project"
    workspace.mkdir()
    provider = DependencyProvider()
    agent = ZXAgentPro(
        provider, workspace,
        config=AgentConfig(store_tool_memory=False),
    )
    events = [event async for event in agent.chat_stream("implement dependency-aware feature")]

    plan = next(event["content"] for event in events if event["type"] == "plan")
    assert plan == ["independent", "prepare", "finish"]
    execution_prompt = next(message["content"] for message in provider.execution_messages
                            if "dependency-safe topological order" in str(message.get("content", "")))
    assert "dependency-safe topological order" in execution_prompt
    assert "do not parallelize workspace mutations" in execution_prompt
    saved = agent.task_state.load()
    assert saved["status"] == "stored"
    graph_nodes = saved["state"]["payload"]["task_graph"]["nodes"]
    assert graph_nodes[0]["dependencies"] == ["step_3"]


@pytest.mark.asyncio
async def test_node_scheduler_executes_dependency_nodes_separately(tmp_path):
    class NodeProvider(RoutedProvider):
        def __init__(self):
            super().__init__("node-provider")
            self.node_messages = []

        async def complete_async(self, system, messages, tools=None):
            self.complete_calls += 1
            return Response('{"objective":"two nodes","steps":['
                '{"description":"prepare module","depends_on":[]},'
                '{"description":"verify module","depends_on":[0]}]}')

        async def stream_complete_async(self, system, messages, tools=None):
            self.stream_calls += 1
            self.node_messages.append([dict(m) for m in messages])
            yield {"type": "text", "content": f"node-{self.stream_calls}-evidence"}
            yield {"type": "done", "content": ""}

    workspace = tmp_path / "node-scheduler"
    workspace.mkdir()
    provider = NodeProvider()
    agent = ZXAgentPro(provider, workspace, config=AgentConfig(store_tool_memory=False))
    events = [event async for event in agent.chat_stream("prepare then verify")]
    assert provider.stream_calls == 2
    assert "Current DAG node: step_1" in str(provider.node_messages[0])
    assert "Current DAG node: step_2" in str(provider.node_messages[1])
    assert "node-1-evidence" in str(provider.node_messages[1])
    state = agent.task_state.load()["state"]["payload"]["task_graph"]
    assert [node["status"] for node in state["nodes"]] == ["completed", "completed"]
    assert any(event["type"] == "done" and event["status"] == "completed" for event in events)


@pytest.mark.asyncio
async def test_resume_skips_completed_nodes_and_runs_only_pending_nodes(tmp_path):
    class ResumeProvider(RoutedProvider):
        def __init__(self):
            super().__init__("resume-provider")
            self.streamed = []

        async def stream_complete_async(self, system, messages, tools=None):
            self.streamed.append([dict(m) for m in messages])
            yield {"type": "text", "content": "resumed-node-evidence"}
            yield {"type": "done", "content": ""}

    workspace = tmp_path / "resume-scheduler"
    workspace.mkdir()
    provider = ResumeProvider()
    agent = ZXAgentPro(provider, workspace, config=AgentConfig(store_tool_memory=False))
    from execution.resume import ExecutionCursor
    cursor = ExecutionCursor()
    cursor.transition("planned")
    graph = {"nodes": [
        {"id": "step_1", "description": "already done", "dependencies": [],
         "status": "completed", "result": "existing evidence"},
        {"id": "step_2", "description": "finish pending work", "dependencies": ["step_1"],
         "status": "pending"},
    ]}
    agent.task_state.save("resume-node-test", "node_step_1_completed", {
        "goal": "complete both steps", "plan": ["already done", "finish pending work"],
        "task_graph": graph, "role_results": [], "execution_cursor": cursor.to_dict(),
    })
    events = [event async for event in agent.resume_task_stream("resume-node-test")]
    assert len(provider.streamed) == 1
    assert "Current DAG node: step_2" in str(provider.streamed[0])
    saved = agent.task_state.load("resume-node-test")["state"]["payload"]["task_graph"]
    assert [node["status"] for node in saved["nodes"]] == ["completed", "completed"]
    assert any(event["type"] == "done" and event["status"] == "completed" for event in events)


@pytest.mark.asyncio
async def test_resume_refuses_running_node_without_replaying_it(tmp_path):
    class NoReplayProvider(RoutedProvider):
        def __init__(self):
            super().__init__("no-replay")
            self.stream_calls = 0

        async def stream_complete_async(self, system, messages, tools=None):
            self.stream_calls += 1
            yield {"type": "text", "content": "must not execute"}
            yield {"type": "done", "content": ""}

    workspace = tmp_path / "no-replay"
    workspace.mkdir()
    provider = NoReplayProvider()
    agent = ZXAgentPro(provider, workspace, config=AgentConfig(store_tool_memory=False))
    from execution.resume import ExecutionCursor
    cursor = ExecutionCursor()
    cursor.transition("executing", current_node_id="step_1", mutation_started=True)
    agent.task_state.save("unsafe-resume", "node_step_1_running", {
        "goal": "do something", "task_graph": {"nodes": [
            {"id": "step_1", "description": "possibly mutated", "dependencies": [], "status": "running"}
        ]}, "execution_cursor": cursor.to_dict(),
    })
    events = [event async for event in agent.resume_task_stream("unsafe-resume")]
    assert provider.stream_calls == 0
    assert any(event["type"] == "error" and "manually" in event["content"] for event in events)


@pytest.mark.asyncio
async def test_resume_blocks_when_workspace_drifted_after_checkpoint(tmp_path):
    class DriftProvider(RoutedProvider):
        def __init__(self):
            super().__init__("drift-provider")
            self.stream_calls = 0

        async def stream_complete_async(self, system, messages, tools=None):
            self.stream_calls += 1
            yield {"type": "text", "content": "must not replay"}
            yield {"type": "done", "content": ""}

    workspace = tmp_path / "drift-workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("before", encoding="utf-8")
    provider = DriftProvider()
    agent = ZXAgentPro(provider, workspace, config=AgentConfig(store_tool_memory=False))
    from execution.resume import ExecutionCursor
    cursor = ExecutionCursor()
    cursor.transition("planned")
    agent.task_state.save("drift-resume", "node_step_1_completed", {
        "goal": "continue safely", "task_graph": {"nodes": [
            {"id": "step_1", "description": "already complete", "dependencies": [],
             "status": "completed", "result": "verified"},
            {"id": "step_2", "description": "pending", "dependencies": ["step_1"],
             "status": "pending"},
        ]}, "execution_cursor": cursor.to_dict(),
    })
    (workspace / "app.py").write_text("external modification", encoding="utf-8")
    events = [event async for event in agent.resume_task_stream("drift-resume")]
    assert provider.stream_calls == 0
    assert any(event["type"] == "error" and "workspace changed" in event["content"] for event in events)


@pytest.mark.asyncio
async def test_chat_stream_holds_workspace_lock_until_stream_closes(tmp_path, monkeypatch):
    import asyncio

    workspace = tmp_path / "locked-project"
    workspace.mkdir()
    agent = ZXAgentPro(RoutedProvider("main"), workspace,
                       config=AgentConfig(store_tool_memory=False))
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_unlocked(prompt, _resume_state=None):
        started.set()
        yield {"type": "status", "content": prompt}
        await release.wait()

    monkeypatch.setattr(agent, "_chat_stream_unlocked", fake_unlocked)
    first = agent.chat_stream("first")
    first_event = await anext(first)
    assert first_event["content"] == "first"
    assert started.is_set()

    second_events = [event async for event in agent.chat_stream("second")]
    assert second_events[0]["type"] == "error"
    assert "already being used" in second_events[0]["content"]

    release.set()
    await first.aclose()
    third_events = [event async for event in agent.chat_stream("third")]
    assert third_events[0]["content"] == "third"
