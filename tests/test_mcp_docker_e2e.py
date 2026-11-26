"""Opt-in real MCP protocol test through the production Docker isolation path.

Build the fixture image, then set ZXCODE_MCP_DOCKER_IMAGE to its digest ref.
"""
import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from zxcode_mcp.client import MCPClient
from zxcode_mcp.security import MCPServerConfig


IMAGE = os.environ.get("ZXCODE_MCP_DOCKER_IMAGE")
pytestmark = pytest.mark.skipif(
    not IMAGE or not shutil.which("docker"),
    reason="set ZXCODE_MCP_DOCKER_IMAGE to run Docker MCP end-to-end test",
)


def test_docker_isolated_mcp_stdio_initialize_list_and_call():
    fixture_dir = Path(__file__).parent / "fixtures" / "mcp_stdio_e2e"
    config = MCPServerConfig(
        name="docker_e2e", transport="stdio",
        command="/usr/local/bin/python", args=("/workspace/server.py",),
        cwd=str(fixture_dir), sandbox_image=IMAGE,
        allowed_tools=frozenset({"echo", "probe_isolation"}), timeout_seconds=20,
    )
    client = MCPClient(config)

    async def exercise():
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {"echo", "probe_isolation"}

        result = await client.call_tool("echo", {"message": "protocol-ok"})
        text_blocks = [item.text for item in result.content if hasattr(item, "text")]
        assert text_blocks == ["fixture-echo:protocol-ok"]
        assert not getattr(result, "isError", False)

        probe = await client.call_tool("probe_isolation", {})
        probe_text = [item.text for item in probe.content if hasattr(item, "text")]
        observed = json.loads(probe_text[0])
        assert observed["root_writable"] is False
        assert observed["workspace_writable"] is False
        assert observed["tmp_writable"] is True
        assert observed["tmp_noexec"] is True
        assert observed["tmp_nosuid"] is True
        assert observed["tmp_nodev"] is True
        assert observed["tmp_max_bytes"] <= 64 * 1024 * 1024
        assert observed["shm_max_bytes"] <= 16 * 1024 * 1024
        assert observed["external_network_blocked"] is True
        assert observed["docker_socket_visible"] is False
        assert observed["effective_capabilities"] == "0000000000000000"
        assert observed["no_new_privileges"] == "1"
        assert observed["nofile_soft_limit"] <= 1024
        assert observed["core_soft_limit"] == 0
        assert observed["pids_limit"] == "64"
        assert observed["memory_limit"] == str(256 * 1024 * 1024)
        assert observed["cpu_limit"] == "100000 100000"

    asyncio.run(exercise())
