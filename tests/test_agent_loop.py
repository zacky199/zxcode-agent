"""Tests for the hardened ReAct loop: retry, anti-loop, compaction, rollback."""
import asyncio
from pathlib import Path

import pytest

from core import ZXAgentPro, AgentConfig


class SimpleResponse:
    def __init__(self, text):
        self.text = text
        self.tool_calls = []
        self.raw = {}


@pytest.mark.asyncio
async def test_retry_on_transient_error(mock_provider, tmp_path):
    """Transient 503 errors during planning/streaming should be retried."""
    provider = mock_provider
    provider.model = "flaky"
    complete_attempts = 0
    original_complete = provider.complete_async

    async def flaky_complete(system, messages, tools=None):
        nonlocal complete_attempts
        complete_attempts += 1
        if complete_attempts <= 2:
            raise RuntimeError("HTTP 503: temporarily unavailable")
        return await original_complete(system, messages, tools)

    async def flaky_stream(system, messages, tools=None):
        provider.stream_calls += 1
        if provider.stream_calls == 1:
            raise RuntimeError("timeout")
        yield {"type": "text", "content": "Writing."}
        yield {"type": "tool_call", "content": {
            "id": "c1", "name": "write_file",
            "arguments": {"file_path": "ok.txt", "content": "retried"}}}
        yield {"type": "done", "content": ""}
        yield {"type": "text", "content": "finished"}

    provider.complete_async = flaky_complete
    provider.stream_complete_async = flaky_stream
    ws = tmp_path / "project"
    ws.mkdir()
    agent = ZXAgentPro(provider, ws, config=AgentConfig(max_retries=3))
    events = [e async for e in agent.chat_stream("buat file")]

    assert not (ws / "ok.txt").exists()
    assert any(event["type"] == "tool_result"
               and event["content"].get("status") == "error"
               and "native file mutation is disabled"
               in event["content"].get("output_preview", "").lower()
               for event in events)
    assert complete_attempts == 3  # 2 failures + 1 success
    assert provider.complete_calls == 1
    assert [e["content"] for e in events if e["type"] == "plan"] == [["step 1"]]
    assert provider.stream_calls >= 2
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_anti_infinite_loop(mock_provider, tmp_path):
    """Identical repeated tool calls must abort the loop."""
    provider = mock_provider
    provider.model = "loop"
    provider.tool_script = [{"type": "tool_call", "content": {
        "id": "c1", "name": "write_file",
        "arguments": {"file_path": "loop.txt", "content": "same"}}}]
    ws = tmp_path / "project"
    ws.mkdir()
    agent = ZXAgentPro(provider, ws,
                       config=AgentConfig(max_repeated_tool_calls=2, max_iterations=50))
    events = [e async for e in agent.chat_stream("loop")]

    assert [e["content"] for e in events if e["type"] == "plan"] == [["step 1"]]
    assert provider.complete_calls == 1
    statuses = [e["content"] for e in events if e["type"] == "status"]
    assert any("repeated" in s for s in statuses)
    # Must not spin the full 50 iterations.
    assert len(events) < 20


@pytest.mark.asyncio
async def test_successful_mutation_survives_later_tool_failures(mock_provider, tmp_path):
    """Later tool failures must not erase an earlier successful edit."""
    provider = mock_provider
    provider.model = "fail"

    async def failing_stream(system, messages, tools=None):
        provider.stream_calls += 1
        if provider.stream_calls == 1:
            yield {"type": "tool_call", "content": {
                "id": "c1", "name": "write_file",
                "arguments": {"file_path": "good.txt", "content": "v1"}}}
            yield {"type": "done", "content": ""}
            yield {"type": "text", "content": "wrote"}
            return
        yield {"type": "tool_call", "content": {
            "id": "cx", "name": "edit_file",
            "arguments": {"file_path": "good.txt",
                          "search": "MISSING-UNIQUE", "replace": "x"}}}
        yield {"type": "done", "content": ""}
        yield {"type": "text", "content": "editing"}

    provider.stream_complete_async = failing_stream
    ws = tmp_path / "project"
    ws.mkdir()
    agent = ZXAgentPro(provider, ws,
                       config=AgentConfig(max_consecutive_failures=2,
                                          max_repeated_tool_calls=3,
                                          max_iterations=30))
    events = [e async for e in agent.chat_stream("ubah file")]

    assert [e["content"] for e in events if e["type"] == "plan"] == [["step 1"]]
    assert provider.complete_calls == 1
    statuses = [e["content"] for e in events if e["type"] == "status"]
    assert any("repeated" in s.lower() for s in statuses)
    assert not any("rolling back" in s.lower() or "workspace restored" in s.lower()
                   for s in statuses)
    assert not (ws / "good.txt").exists()
    assert sum(event["type"] == "tool_result"
               and event["content"].get("status") == "error"
               for event in events) >= 2


def test_config_defaults():
    cfg = AgentConfig()
    assert cfg.max_retries == 3
    assert cfg.max_iterations == 30
    assert cfg.planning == "auto"
    assert cfg.max_consecutive_failures == 4


@pytest.mark.asyncio
async def test_auto_rollback_ignores_untrusted_latest_empty_zip(tmp_path):
    import zipfile

    from config.constants import CHECKPOINT_DIR_NAME
    from execution.checkpoint import CheckpointManager
    from execution.rollback import RollbackManager

    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "keep.txt"
    target.write_text("before", encoding="utf-8")
    checkpoints = CheckpointManager(workspace)
    snapshot = checkpoints.create_snapshot("pre_mutation")
    target.write_text("changed", encoding="utf-8")

    rogue = workspace / CHECKPOINT_DIR_NAME / "pre_mutation_99999999_999999_999999.zip"
    with zipfile.ZipFile(rogue, "w"):
        pass

    agent = object.__new__(ZXAgentPro)
    agent.config = AgentConfig(auto_rollback=True)
    agent.checkpoints = checkpoints
    agent.rollback = RollbackManager(workspace, trusted_snapshots=checkpoints.trusted_snapshots)

    assert await agent._auto_rollback(snapshot) is True
    assert target.read_text(encoding="utf-8") == "before"
    assert rogue.exists()


@pytest.mark.asyncio
async def test_later_prompt_failure_preserves_user_file_after_blocked_write(tmp_path):
    class TwoPromptProvider:
        model = "two-prompt"
        supports_tools = True

        def __init__(self):
            self.stream_calls = 0

        async def complete_async(self, system, messages, tools=None):
            return SimpleResponse(
                '{"objective":"task","steps":[{"description":"step 1","depends_on":[]}]}'
            )

        async def stream_complete_async(self, system, messages, tools=None):
            self.stream_calls += 1
            if self.stream_calls == 1:
                yield {"type": "tool_call", "content": {
                    "id": "write-a", "name": "write_file",
                    "arguments": {"file_path": "from_prompt_a.txt", "content": "keep"}}}
                yield {"type": "done", "content": ""}
            elif self.stream_calls == 2:
                yield {"type": "text", "content": "prompt A complete"}
                yield {"type": "done", "content": ""}
            else:
                yield {"type": "tool_call", "content": {
                    "id": f"read-b-{self.stream_calls}", "name": "read_file",
                    "arguments": {"file_path": "missing.txt"}}}
                yield {"type": "done", "content": ""}

    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "from_prompt_a.txt"
    target.write_text("user-owned original", encoding="utf-8")
    agent = ZXAgentPro(
        TwoPromptProvider(), workspace,
        config=AgentConfig(max_consecutive_failures=2, max_iterations=10,
                           sandbox_mode="host", allow_host_execution=True),
    )

    prompt_a_events = [event async for event in agent.chat_stream("prompt A writes")]
    assert target.read_text(encoding="utf-8") == "user-owned original"
    assert next(event["content"] for event in prompt_a_events if event["type"] == "plan") == ["step 1"]

    prompt_b_events = [event async for event in agent.chat_stream("prompt B read errors")]

    assert any("repeated" in event["content"].lower()
               for event in prompt_b_events if event["type"] == "status")
    assert target.read_text(encoding="utf-8") == "user-owned original"
    assert not any(event["content"] == "Workspace restored to last checkpoint."
                   for event in prompt_b_events if event["type"] == "status")


@pytest.mark.asyncio
async def test_run_command_partial_mutation_is_preserved_as_ambiguous_after_nonzero_exit(tmp_path):
    import shlex
    import sys

    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "partial.txt"
    code = f"from pathlib import Path; Path({str(target)!r}).write_text('partial'); raise SystemExit(1)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"

    class PartialCommandProvider:
        model = "partial-command"
        supports_tools = True

        async def complete_async(self, system, messages, tools=None):
            return SimpleResponse(
                '{"objective":"command","steps":[{"description":"run command","depends_on":[]}]}'
            )

        async def stream_complete_async(self, system, messages, tools=None):
            yield {"type": "tool_call", "content": {
                "id": "partial-command", "name": "run_command",
                "arguments": {"command": command}}}
            yield {"type": "done", "content": ""}

    approvals = []
    agent = ZXAgentPro(
        PartialCommandProvider(), workspace,
        confirm_callback=lambda cmd: approvals.append(cmd) or True,
        config=AgentConfig(max_consecutive_failures=1, max_repeated_tool_calls=1,
                           max_iterations=3, sandbox_mode="host", allow_host_execution=True),
    )

    events = [event async for event in agent.chat_stream("write then fail")]

    assert approvals == [command]
    assert any(event["type"] == "tool_result" and event["content"]["status"] == "error"
               for event in events)
    assert any("conflict/ambiguous" in event["content"].get("output_preview", "").lower()
               for event in events if event["type"] == "tool_result")
    assert target.read_text(encoding="utf-8") == "partial"


@pytest.mark.asyncio
async def test_markdown_file_and_patch_fallbacks_are_blocked_without_mutation(tmp_path):
    action_text = """```file partial.md
partial change
```
```patch partial.md
<<<< SEARCH
missing text
====
replacement
>>>> REPLACE
```"""

    class MarkdownFallbackProvider:
        model = "markdown-fallback"
        supports_tools = True

        async def complete_async(self, system, messages, tools=None):
            return SimpleResponse(
                '{"objective":"markdown","steps":[{"description":"file action","depends_on":[]}]}'
            )

        async def stream_complete_async(self, system, messages, tools=None):
            yield {"type": "text", "content": action_text}
            yield {"type": "done", "content": ""}

    workspace = tmp_path / "project"
    workspace.mkdir()
    agent = ZXAgentPro(
        MarkdownFallbackProvider(), workspace,
        config=AgentConfig(max_consecutive_failures=1, max_iterations=3),
    )

    events = [event async for event in agent.chat_stream("use markdown fallback")]

    assert not any("workspace restored" in event["content"].lower()
                   for event in events if event["type"] == "status")
    assert not (workspace / "partial.md").exists()


@pytest.mark.asyncio
async def test_markdown_bash_mutation_is_preserved_as_ambiguous_with_active_task(tmp_path):
    import shlex
    import sys

    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "markdown-bash-partial.txt"
    code = f"from pathlib import Path; Path({str(target)!r}).write_text('partial'); raise SystemExit(1)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"

    class MarkdownBashProvider:
        model = "markdown-bash"
        supports_tools = False

        def __init__(self):
            self.stream_calls = 0

        async def complete_async(self, system, messages, tools=None):
            return SimpleResponse(
                '{"objective":"markdown bash","steps":[{"description":"run action",'
                '"depends_on":[]}]}'
            )

        async def stream_complete_async(self, system, messages, tools=None):
            self.stream_calls += 1
            if self.stream_calls == 1:
                yield {"type": "text", "content": f"```bash\n{command}\n```"}
            else:
                yield {"type": "tool_call", "content": {
                    "id": "fail-after-markdown", "name": "read_file",
                    "arguments": {"file_path": "missing-after-markdown.txt"}}}
            yield {"type": "done", "content": ""}

    agent = ZXAgentPro(
        MarkdownBashProvider(), workspace,
        confirm_callback=lambda candidate: candidate == command,
        config=AgentConfig(max_consecutive_failures=1, max_iterations=4,
                           sandbox_mode="host", allow_host_execution=True),
    )
    events = [event async for event in agent.chat_stream("run a failing markdown command")]

    assert not any("workspace restored" in event["content"].lower()
                   for event in events if event["type"] == "status")
    assert target.read_text(encoding="utf-8") == "partial"


@pytest.mark.asyncio
async def test_markdown_bash_partial_mutation_is_preserved_as_ambiguous_active_prompt(tmp_path):
    import shlex
    import sys

    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "markdown-partial.txt"
    code = f"from pathlib import Path; Path({str(target)!r}).write_text('partial'); raise SystemExit(1)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    action_text = f"```bash\n{command}\n```"

    class MarkdownBashProvider:
        model = "markdown-bash"
        supports_tools = True

        async def complete_async(self, system, messages, tools=None):
            return SimpleResponse(
                '{"objective":"bash","steps":[{"description":"bash action","depends_on":[]}]}'
            )

        async def stream_complete_async(self, system, messages, tools=None):
            yield {"type": "text", "content": action_text}
            yield {"type": "done", "content": ""}

    approvals = []
    agent = ZXAgentPro(
        MarkdownBashProvider(), workspace,
        confirm_callback=lambda cmd: approvals.append(cmd) or True,
        config=AgentConfig(max_consecutive_failures=1, max_repeated_tool_calls=1,
                           max_iterations=1, sandbox_mode="host", allow_host_execution=True),
    )

    events = [event async for event in agent.chat_stream("markdown bash write then fail")]

    assert approvals == [command]
    assert not any("workspace restored" in event["content"].lower()
                   for event in events if event["type"] == "status")
    assert target.read_text(encoding="utf-8") == "partial"


@pytest.mark.asyncio
async def test_invalid_planner_json_warns_uses_original_single_step_and_keeps_tool_approval(tmp_path):
    import shlex
    import sys

    request = "buat laporan singkat dari project"
    workspace = tmp_path / "project"
    workspace.mkdir()
    marker = workspace / "unapproved-command-ran.txt"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    approved_commands = []

    class InvalidPlannerProvider:
        model = "invalid-planner-json"
        supports_tools = True

        def __init__(self):
            self.stream_calls = 0

        async def complete_async(self, system, messages, tools=None):
            return SimpleResponse("this is not valid JSON")

        async def stream_complete_async(self, system, messages, tools=None):
            self.stream_calls += 1
            if self.stream_calls == 1:
                yield {"type": "tool_call", "content": {
                    "id": "unapproved-command", "name": "run_command",
                    "arguments": {"command": command}}}
            else:
                yield {"type": "text", "content": "command approval was checked"}
            yield {"type": "done", "content": ""}

    agent = ZXAgentPro(
        InvalidPlannerProvider(), workspace,
        confirm_callback=lambda value: approved_commands.append(value) or False,
        config=AgentConfig(max_iterations=3),
    )
    events = [event async for event in agent.chat_stream(request)]

    plan_event = next(event for event in events if event["type"] == "plan")
    assert plan_event["content"] == [request]
    assert len(plan_event["content"]) == 1
    assert plan_event["status"] == "fallback"
    assert "without decomposition" in plan_event["warning"].lower()
    assert any(
        event["type"] == "status"
        and event.get("status") == "planner_fallback"
        and "not readable" in event["content"].lower()
        and "without decomposition" in event["content"].lower()
        for event in events
    )
    assert approved_commands == [command]
    assert not marker.exists()


@pytest.mark.asyncio
async def test_failed_verification_gets_one_repair_pass_without_erasing_edits(tmp_path):
    import shlex
    import sys

    command = "python -c 'raise SystemExit(1)'"
    passing_command = "python -c 'pass'"
    app_path = tmp_path / "project" / "app.txt"
    initial_code = f"from pathlib import Path; Path({str(app_path)!r}).write_text('initial edit')"
    repaired_code = f"from pathlib import Path; Path({str(app_path)!r}).write_text('repaired edit')"
    initial_command = f"{shlex.quote(sys.executable)} -c {shlex.quote(initial_code)}"
    repaired_command = f"{shlex.quote(sys.executable)} -c {shlex.quote(repaired_code)}"
    actions = [
        ("run_command", {"command": initial_command}),
        ("run_command", {"command": command}),
        ("run_command", {"command": repaired_command}),
        ("run_command", {"command": passing_command}),
        ("run_command", {"command": command}),
    ]

    class RepairProvider:
        model = "repair-test"
        supports_tools = True

        def __init__(self):
            self.index = 0
            self.repair_messages = []

        async def complete_async(self, system, messages, tools=None):
            return SimpleResponse(
                '{"objective":"repair","steps":[{"description":"edit and verify",'
                '"depends_on":[]}]}'
            )

        async def stream_complete_async(self, system, messages, tools=None):
            self.repair_messages.extend(
                str(message.get("content", "")) for message in messages
                if "[BOUNDED REPAIR REQUEST" in str(message.get("content", ""))
            )
            name, args = actions[self.index]
            self.index += 1
            yield {"type": "tool_call", "content": {
                "id": f"repair-{self.index}", "name": name, "arguments": args}}
            yield {"type": "done", "content": ""}

    workspace = tmp_path / "project"
    workspace.mkdir()
    provider = RepairProvider()
    agent = ZXAgentPro(
        provider, workspace,
        confirm_callback=lambda reviewed: reviewed in (
            initial_command, repaired_command, command, passing_command),
        config=AgentConfig(max_iterations=10, sandbox_mode="host", allow_host_execution=True),
    )

    events = [event async for event in agent.chat_stream("edit, test, repair once")]

    assert (workspace / "app.txt").read_text(encoding="utf-8") == "repaired edit"
    statuses = [event["content"].lower() for event in events if event["type"] == "status"]
    assert sum("one repair pass is available" in status for status in statuses) == 1
    assert any("verification passed" in status for status in statuses)
    assert any("still fails after one repair pass" in status for status in statuses)
    assert provider.repair_messages
    assert "untrusted diagnostic data" in provider.repair_messages[0]
    assert "smallest in-scope correction" in provider.repair_messages[0]
    diff_summary = next(event["content"] for event in events if event["type"] == "diff_summary")
    assert "app.txt" in diff_summary
    assert "manual undo" in diff_summary.lower()
