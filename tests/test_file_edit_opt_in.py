"""Native file edits are opt-in: off by default, user-enabled, never model-enabled."""
import asyncio

import pytest

from core import AgentConfig, ZXAgentPro
from core.agent import build_system_prompt
from tools import ToolEngine
from tools.builtin import BuiltinTools

BLOCKED = "native file mutation is disabled"


def _run(engine, name, args, journal):
    return asyncio.run(engine.execute_tool(name, args, journal=journal))


@pytest.fixture()
def enabled_engine(workspace):
    return ToolEngine(workspace, allow_file_mutations=True)


# --------------------------------------------------------------------------
# default state and schemas
# --------------------------------------------------------------------------
def test_default_engine_has_edits_off_and_hides_tools(workspace):
    engine = ToolEngine(workspace)
    names = {s["function"]["name"] for s in engine.get_tool_schemas()}
    assert engine.file_mutations_enabled is False
    assert not {"write_file", "edit_file"} & names
    assert {"read_file", "run_command", "delegate_tasks"} <= names


def test_enabled_engine_advertises_real_tool_descriptions(enabled_engine):
    schemas = {s["function"]["name"]: s["function"]["description"]
               for s in enabled_engine.get_tool_schemas()}
    assert {"write_file", "edit_file"} <= set(schemas)
    for name in ("write_file", "edit_file"):
        assert "unavailable" not in schemas[name].lower()
        assert "disabled" not in schemas[name].lower()


def test_toggle_updates_schemas_both_ways(workspace):
    engine = ToolEngine(workspace)
    engine.set_file_mutations_enabled(True)
    assert "write_file" in {s["function"]["name"] for s in engine.get_tool_schemas()}
    engine.set_file_mutations_enabled(False)
    assert "write_file" not in {s["function"]["name"] for s in engine.get_tool_schemas()}


# --------------------------------------------------------------------------
# behaviour when enabled
# --------------------------------------------------------------------------
def test_enabled_write_and_edit_work_and_are_journaled(enabled_engine, workspace):
    (workspace / "a.txt").write_text("hello world\n", encoding="utf-8")
    journal = enabled_engine.create_mutation_journal()
    try:
        wrote = _run(enabled_engine, "write_file",
                     {"file_path": "new.txt", "content": "fresh\n"}, journal)
        edited = _run(enabled_engine, "edit_file",
                      {"file_path": "a.txt", "search": "world", "replace": "zacky"}, journal)
    finally:
        summary = journal.close()

    assert wrote["status"] == "ok" and edited["status"] == "ok"
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "fresh\n"
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "hello zacky\n"
    assert "new.txt" in summary and "a.txt" in summary


def test_enabled_write_into_existing_subdirectory(enabled_engine, workspace):
    (workspace / "src").mkdir()
    journal = enabled_engine.create_mutation_journal()
    try:
        result = _run(enabled_engine, "write_file",
                      {"file_path": "src/mod.py", "content": "x = 1\n"}, journal)
    finally:
        journal.close()
    assert result["status"] == "ok"
    assert (workspace / "src" / "mod.py").read_text(encoding="utf-8") == "x = 1\n"


def test_enabled_write_does_not_create_missing_parent_directory(enabled_engine, workspace):
    journal = enabled_engine.create_mutation_journal()
    try:
        result = _run(enabled_engine, "write_file",
                      {"file_path": "newdir/file.txt", "content": "x"}, journal)
    finally:
        journal.close()
    assert result["status"] == "error"
    assert not (workspace / "newdir").exists()


@pytest.mark.parametrize("target", [".env", "keys/id_rsa", "../escape.txt"])
def test_enabled_edits_still_block_secrets_and_escapes(enabled_engine, workspace, target):
    journal = enabled_engine.create_mutation_journal()
    try:
        result = _run(enabled_engine, "write_file",
                      {"file_path": target, "content": "SECRET=1"}, journal)
    finally:
        journal.close()
    assert result["status"] == "error"
    assert not (workspace / ".env").exists()
    assert not (workspace.parent / "escape.txt").exists()


def test_markdown_file_and_patch_work_when_enabled(enabled_engine, workspace):
    (workspace / "m.txt").write_text("alpha\n", encoding="utf-8")
    journal = enabled_engine.create_mutation_journal()
    try:
        output = asyncio.run(enabled_engine.execute_markdown_actions(
            "```file created.txt\nfrom markdown\n```\n"
            "```patch m.txt\n<<<< SEARCH\nalpha\n====\nbeta\n>>>> REPLACE\n```",
            journal=journal))
    finally:
        journal.close()
    assert "error" not in output.lower()
    assert (workspace / "created.txt").exists()
    assert (workspace / "m.txt").read_text(encoding="utf-8") == "beta\n"


def test_turning_edits_off_blocks_again(enabled_engine, workspace):
    enabled_engine.set_file_mutations_enabled(False)
    journal = enabled_engine.create_mutation_journal()
    try:
        result = _run(enabled_engine, "write_file",
                      {"file_path": "x.txt", "content": "x"}, journal)
    finally:
        journal.close()
    assert result["status"] == "error"
    assert BLOCKED in result["output"].lower()
    assert not (workspace / "x.txt").exists()


# --------------------------------------------------------------------------
# the model cannot enable edits
# --------------------------------------------------------------------------
@pytest.mark.parametrize("flag", ["allow_file_mutations", "allow_file_edits",
                                  "file_mutations_enabled", "enabled"])
def test_model_arguments_cannot_enable_edits(workspace, flag):
    engine = ToolEngine(workspace)
    journal = engine.create_mutation_journal()
    try:
        result = _run(engine, "write_file",
                      {"file_path": "x.txt", "content": "x", flag: True}, journal)
    finally:
        journal.close()
    assert result["status"] == "error"
    assert engine.file_mutations_enabled is False
    assert not (workspace / "x.txt").exists()


def test_markdown_text_cannot_enable_edits(workspace):
    engine = ToolEngine(workspace)
    asyncio.run(engine.execute_markdown_actions(
        "/edits on\n```file x.txt\nx\n```", journal=engine.create_mutation_journal()))
    assert engine.file_mutations_enabled is False
    assert not (workspace / "x.txt").exists()


def test_builtin_tools_default_off_and_cannot_skip_the_journal_gate(workspace):
    off = BuiltinTools(workspace)
    assert BLOCKED in off.write_file("x.txt", "x").lower()

    on = BuiltinTools(workspace, allow_file_mutations=True)
    output = on.write_file("x.txt", "x")  # no journal callbacks supplied
    assert "journal gate" in output.lower()
    assert not (workspace / "x.txt").exists()


# --------------------------------------------------------------------------
# prompt follows the switch
# --------------------------------------------------------------------------
def test_system_prompt_matches_tool_availability():
    off = build_system_prompt(False)
    on = build_system_prompt(True)
    assert "{file_edit_guidance}" not in off and "{file_edit_guidance}" not in on
    assert "NOT available" in off and "--allow-edits" in off
    assert "Use write_file to create files" not in off
    assert "Use write_file to create files" in on
    assert "mkdir -p" in on


# --------------------------------------------------------------------------
# end to end through the agent loop
# --------------------------------------------------------------------------
def _scripted_write(provider):
    provider.tool_script = [{"type": "tool_call", "content": {
        "id": "c1", "name": "write_file",
        "arguments": {"file_path": "made.txt", "content": "by agent\n"}}}]


@pytest.mark.asyncio
async def test_agent_writes_file_only_when_edits_enabled(mock_provider, tmp_path):
    ws_on = tmp_path / "on"
    ws_off = tmp_path / "off"
    ws_on.mkdir()
    ws_off.mkdir()
    config = AgentConfig(max_repeated_tool_calls=2, max_iterations=4)

    _scripted_write(mock_provider)
    agent_on = ZXAgentPro(mock_provider, ws_on, config=config, allow_file_edits=True)
    events_on = [e async for e in agent_on.chat_stream("buat file")]
    assert (ws_on / "made.txt").read_text(encoding="utf-8") == "by agent\n"
    assert any(e["type"] == "tool_result" and e["content"]["status"] == "ok"
               for e in events_on)

    agent_off = ZXAgentPro(mock_provider, ws_off, config=config)
    events_off = [e async for e in agent_off.chat_stream("buat file")]
    assert not (ws_off / "made.txt").exists()
    assert any(e["type"] == "tool_result" and BLOCKED in
               e["content"].get("output_preview", "").lower() for e in events_off)


@pytest.mark.asyncio
async def test_agent_toggle_takes_effect_on_next_request(mock_provider, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _scripted_write(mock_provider)
    agent = ZXAgentPro(mock_provider, ws,
                       config=AgentConfig(max_repeated_tool_calls=2, max_iterations=4))
    assert agent.tool_engine.file_mutations_enabled is False

    _ = [e async for e in agent.chat_stream("coba")]
    assert not (ws / "made.txt").exists()

    agent.tool_engine.set_file_mutations_enabled(True)
    _ = [e async for e in agent.chat_stream("coba lagi")]
    assert (ws / "made.txt").exists()
