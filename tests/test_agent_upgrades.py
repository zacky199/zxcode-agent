"""Agent-loop integration tests: planner triage, project instructions, output
truncation, parallel read-only tools, structured compaction, session persistence."""
import asyncio
import json

import pytest

from core import AgentConfig, ZXAgentPro
from core.agent import SUMMARY_PREFIX
from memory.session_store import SessionStore


def _record(provider):
    """Wrap the provider so every stream call's (system, messages) is captured."""
    calls = []
    original = provider.stream_complete_async

    async def recording(system, messages, tools=None):
        calls.append({"system": system, "messages": [dict(m) for m in messages]})
        async for event in original(system, messages, tools=tools):
            yield event

    provider.stream_complete_async = recording
    return calls


def _call(call_id, name, **arguments):
    return {"type": "tool_call", "content": {"id": call_id, "name": name, "arguments": arguments}}


async def _chat(agent, prompt):
    return [event async for event in agent.chat_stream(prompt)]


# ------------------------------------------------------------------ config
def test_config_validates_new_options():
    cfg = AgentConfig(planning="bogus", max_tool_output_chars=10)
    assert cfg.planning == "auto"
    assert cfg.max_tool_output_chars == 500
    assert AgentConfig(planning="never").planning == "never"


# ----------------------------------------------------------------- planner
@pytest.mark.asyncio
async def test_greeting_skips_planner_and_sends_plain_prompt(mock_provider, tmp_path):
    mock_provider.text_script = [{"type": "text", "content": "Halo juga!"}]
    calls = _record(mock_provider)
    agent = ZXAgentPro(mock_provider, tmp_path)
    events = await _chat(agent, "halo")

    assert mock_provider.complete_calls == 0
    assert not any(e["type"] == "plan" for e in events)
    assert any("skipping the planning step" in str(e.get("content", "")).lower()
               for e in events if e["type"] == "status")
    assert calls[0]["messages"] == [{"role": "user", "content": "halo"}]  # no duplicate prompt
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_tasks_are_still_planned_in_auto_mode(mock_provider, tmp_path):
    mock_provider.text_script = [{"type": "text", "content": "ok"}]
    calls = _record(mock_provider)
    events = await _chat(ZXAgentPro(mock_provider, tmp_path), "buat halaman login")

    assert mock_provider.complete_calls == 1
    assert [e["content"] for e in events if e["type"] == "plan"] == [["step 1"]]
    assert "Plan to follow" in calls[0]["messages"][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,prompt,expect_plan", [
    ("never", "buat halaman login", False),
    ("always", "halo", True),
])
async def test_planning_mode_overrides_triage(mock_provider, tmp_path, mode, prompt, expect_plan):
    mock_provider.text_script = [{"type": "text", "content": "ok"}]
    agent = ZXAgentPro(mock_provider, tmp_path, config=AgentConfig(planning=mode))
    events = await _chat(agent, prompt)
    assert any(e["type"] == "plan" for e in events) is expect_plan
    assert (mock_provider.complete_calls == 1) is expect_plan


# ------------------------------------------------------- project instructions
@pytest.mark.asyncio
async def test_project_instructions_reach_the_system_prompt(mock_provider, tmp_path):
    (tmp_path / "AGENTS.md").write_text("Always use tabs for indentation.")
    mock_provider.text_script = [{"type": "text", "content": "ok"}]
    calls = _record(mock_provider)
    await _chat(ZXAgentPro(mock_provider, tmp_path), "halo")

    system = calls[0]["system"]
    assert "Always use tabs for indentation." in system
    assert "untrusted guidance" in system


@pytest.mark.asyncio
async def test_system_prompt_mentions_new_tool_capabilities(mock_provider, tmp_path):
    mock_provider.text_script = [{"type": "text", "content": "ok"}]
    calls = _record(mock_provider)
    await _chat(ZXAgentPro(mock_provider, tmp_path), "halo")
    system = calls[0]["system"]
    assert "start_line/end_line" in system and "timeout" in system


# ------------------------------------------------------- output truncation
@pytest.mark.asyncio
async def test_huge_tool_output_is_shortened_in_model_context(mock_provider, tmp_path):
    mock_provider.tool_script = [
        _call("c1", "run_command", command="python3 -c \"print('x' * 60000)\"")]
    calls = _record(mock_provider)
    agent = ZXAgentPro(mock_provider, tmp_path,
                       confirm_callback=lambda command: True,
                       config=AgentConfig(max_iterations=3, max_repeated_tool_calls=3,
                                          max_tool_output_chars=2000, sandbox_mode="host",
                                          allow_host_execution=True))
    events = await _chat(agent, "jalankan skrip")

    tool_messages = [m for m in calls[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages, "the second model call should include the tool result"
    content = tool_messages[0]["content"]
    assert len(content) < 2500 and "characters omitted" in content
    # The user-facing event keeps its own (separately capped) preview.
    assert any(e["type"] == "tool_result" and e["content"]["status"] == "ok" for e in events)


# --------------------------------------------------------- parallel reads
def _fake_executor(agent, record):
    state = {"active": 0, "peak": 0}

    async def fake(name, args, journal=None):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        record.append(name)
        return {"tool": name, "status": "ok", "output": f"out:{args.get('file_path', name)}"}

    agent.tool_engine.execute_tool = fake
    return state


@pytest.mark.asyncio
async def test_read_only_calls_in_one_turn_run_concurrently_in_order(mock_provider, tmp_path):
    mock_provider.tool_script = [_call("c1", "read_file", file_path="a.py"),
                                 _call("c2", "read_file", file_path="b.py"),
                                 _call("c3", "git_status")]
    agent = ZXAgentPro(mock_provider, tmp_path, config=AgentConfig(max_iterations=1))
    state = _fake_executor(agent, [])
    events = await _chat(agent, "baca file")

    assert state["peak"] == 3
    shown = [(e["type"], e["content"].get("name")) for e in events
             if e["type"] in ("tool_call", "tool_result")]
    assert shown == [("tool_call", "read_file"), ("tool_result", "read_file"),
                     ("tool_call", "read_file"), ("tool_result", "read_file"),
                     ("tool_call", "git_status"), ("tool_result", "git_status")]


@pytest.mark.asyncio
async def test_mixed_batches_with_commands_stay_sequential(mock_provider, tmp_path):
    mock_provider.tool_script = [_call("c1", "read_file", file_path="a.py"),
                                 _call("c2", "run_command", command="echo hi")]
    agent = ZXAgentPro(mock_provider, tmp_path, config=AgentConfig(max_iterations=1))
    state = _fake_executor(agent, [])
    await _chat(agent, "baca lalu jalankan")
    assert state["peak"] == 1


# ------------------------------------------------------------- compaction
def _bulk(text):
    return text + " " + "pad " * 400


def _history():
    def tool_turn(user_text, call_id, name, args, result):
        return [
            {"role": "user", "content": _bulk(user_text)},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": call_id, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(args)}}]},
            {"role": "tool", "tool_call_id": call_id, "content": _bulk(result)},
            {"role": "assistant", "content": "done " + user_text},
        ]
    return (tool_turn("first request", "a", "read_file", {"file_path": "src/app.py"}, "Error: no such file")
            + tool_turn("second request", "b", "run_command", {"command": "pytest -q"},
                        "1 failed\n(exit code 1)")
            + tool_turn("third request", "c", "list_directory", {"path": "src"}, "listing")
            + [{"role": "user", "content": "latest request"}])


def test_compaction_summary_is_structured(mock_provider, tmp_path):
    agent = ZXAgentPro(mock_provider, tmp_path,
                       config=AgentConfig(max_context_tokens=1000, compaction_threshold=0.1))
    compacted = agent._compact_messages(_history(), "system")

    summaries = [m for m in compacted if m["role"] == "system"]
    assert len(summaries) == 1 and summaries[0]["content"].startswith(SUMMARY_PREFIX)
    text = summaries[0]["content"]
    assert "read_file(src/app.py)" in text and "run_command(pytest -q)" in text
    assert "! failed: Error: no such file" in text
    assert "(exit code 1)" in text
    # The newest complete turn stays verbatim instead of being summarized.
    assert "list_directory(src)" not in text
    assert any(m.get("tool_call_id") == "c" for m in compacted)
    assert compacted[-1] == {"role": "user", "content": "latest request"}
    assert len(text) < 3200


def test_repeated_compaction_keeps_a_single_summary(mock_provider, tmp_path):
    agent = ZXAgentPro(mock_provider, tmp_path,
                       config=AgentConfig(max_context_tokens=1000, compaction_threshold=0.1))
    once = agent._compact_messages(_history(), "system")
    more = once[:-1] + _history()[:-1] + [{"role": "user", "content": "newest request"}]
    twice = agent._compact_messages(more, "system")

    summaries = [m for m in twice if m["role"] == "system"]
    assert len(summaries) == 1
    assert "read_file(src/app.py)" in summaries[0]["content"]
    assert twice[-1]["content"] == "newest request"


# --------------------------------------------------------------- sessions
@pytest.mark.asyncio
async def test_conversation_is_saved_and_restored(mock_provider, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    store = SessionStore(tmp_path / "sessions")
    mock_provider.text_script = [{"type": "text", "content": "Halo!"}]

    first = ZXAgentPro(mock_provider, ws, session_store=store)
    await _chat(first, "halo")
    assert [m["content"] for m in store.load(ws)] == ["halo", "Halo!"]

    calls = _record(mock_provider)
    second = ZXAgentPro(mock_provider, ws, session_store=store)
    assert second.restore_session() == 2
    await _chat(second, "terima kasih")
    sent = calls[0]["messages"]
    assert [(m["role"], m["content"]) for m in sent] == [
        ("user", "halo"), ("assistant", "Halo!"), ("user", "terima kasih")]


@pytest.mark.asyncio
async def test_reset_conversation_clears_memory_and_saved_copy(mock_provider, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    store = SessionStore(tmp_path / "sessions")
    mock_provider.text_script = [{"type": "text", "content": "ok"}]
    agent = ZXAgentPro(mock_provider, ws, session_store=store)
    await _chat(agent, "halo")
    assert store.load(ws)

    agent.reset_conversation()
    assert agent.memory.get_chat_history() == []
    assert store.load(ws) == []
    assert ZXAgentPro(mock_provider, ws, session_store=store).restore_session() == 0


@pytest.mark.asyncio
async def test_agent_without_store_never_persists(mock_provider, tmp_path):
    mock_provider.text_script = [{"type": "text", "content": "ok"}]
    agent = ZXAgentPro(mock_provider, tmp_path)
    await _chat(agent, "halo")
    assert agent.save_session() is False and agent.restore_session() == 0


@pytest.mark.asyncio
async def test_unwritable_session_store_does_not_break_chat(mock_provider, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    mock_provider.text_script = [{"type": "text", "content": "ok"}]
    agent = ZXAgentPro(mock_provider, tmp_path, session_store=SessionStore(blocker / "sessions"))
    events = await _chat(agent, "halo")
    assert events[-1]["type"] == "done"
