"""Tests for sub-agent parallel delegation."""
import asyncio
import tempfile
from pathlib import Path

import pytest

from core.subagent import SubAgentManager, SubAgent
from core import ZXAgentPro
from tools import ToolEngine


class WriteProvider:
    """Sub-agent provider: writes one file, then stops."""
    model = "sub"
    supports_tools = True

    def __init__(self, path, content):
        self.path = path
        self.content = content
        self.calls = 0

    async def complete_async(self, system, messages, tools=None):
        return SimpleResponse('["1"]')

    async def stream_complete_async(self, system, messages, tools=None):
        self.calls += 1
        yield {"type": "tool_call", "content": {
            "id": f"s{self.calls}", "name": "write_file",
            "arguments": {"file_path": self.path, "content": self.content}}}
        yield {"type": "done", "content": ""}
        yield {"type": "text", "content": "sub done"}


class SimpleResponse:
    def __init__(self, text):
        self.text = text
        self.tool_calls = []
        self.raw = {}


@pytest.mark.asyncio
async def test_parallel_subagents(workspace):
    provider = WriteProvider("direct.txt", "direct-content")
    mgr = SubAgentManager(workspace, ToolEngine(workspace), provider)
    results = await mgr.run_many([
        {"name": "t1", "task": "a"},
        {"name": "t2", "task": "b"},
    ])
    assert len(results) == 2
    assert all(r.ok for r in results)
    assert not (workspace / "direct.txt").exists()
    assert all("direct-content" not in result.text for result in results)
    assert not (workspace / ".zx_checkpoints").exists()


def test_format_results():
    from core.subagent import SubAgentResult
    r = SubAgentResult("a", "done work", tool_calls=3, ok=True)
    out = SubAgentManager.format_results(None, [r])
    assert "a" in out and "done work" in out


@pytest.mark.asyncio
async def test_delegate_tool_in_loop(workspace):
    class DelegateProvider:
        model = "delegate"
        supports_tools = True

        def __init__(self):
            self.n = 0

        async def complete_async(self, system, messages, tools=None):
            return SimpleResponse('["1"]')

        async def stream_complete_async(self, system, messages, tools=None):
            self.n += 1
            if self.n == 1:
                yield {"type": "tool_call", "content": {
                    "id": "d1", "name": "delegate_tasks",
                    "arguments": {"tasks": [
                        {"name": "a", "task": "t"}, {"name": "b", "task": "t"}]}}}
                yield {"type": "done", "content": ""}
                yield {"type": "text", "content": "delegated"}
            else:
                yield {"type": "text", "content": "finished"}

    provider = DelegateProvider()
    # Sub-agents reuse the SAME provider (mock) so they can also write files.
    class SubAgentProvider:
        model = "sub"
        supports_tools = True
        async def complete_async(self, system, messages, tools=None):
            return SimpleResponse('["1"]')
        async def stream_complete_async(self, system, messages, tools=None):
            yield {"type": "text", "content": "sub summary"}

    from core.subagent import SubAgentManager as SAM
    agent = ZXAgentPro(provider, workspace)
    # Override sub-agent provider to avoid recursion through the loop mock.
    agent.subagents = SAM(workspace, agent.tool_engine, SubAgentProvider())
    events = [e async for e in agent.chat_stream("delegasikan")]
    calls = [e for e in events if e["type"] == "tool_call"
             and e["content"].get("name") == "delegate_tasks"]
    assert calls
