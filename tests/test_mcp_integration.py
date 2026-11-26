"""Agent-loop MCP integration guidance tests."""
from core.mcp_integration import augment_system_prompt, has_discovered_mcp_tools


def test_mcp_guidance_is_added_only_when_remote_tools_are_advertised():
    base = "base system prompt"
    no_remote_tools = [{"type": "function", "function": {"name": "read_file"}}]
    mcp_tools = [{"type": "function", "function": {"name": "mcp__docs__search"}}]

    assert not has_discovered_mcp_tools(no_remote_tools)
    assert augment_system_prompt(base, no_remote_tools) == base
    assert has_discovered_mcp_tools(mcp_tools)
    augmented = augment_system_prompt(base, mcp_tools)
    assert augmented.startswith(base)
    assert "External MCP Tool Policy" in augmented
    assert "untrusted external data" in augmented
    assert "local approval prompt" in augmented
    assert "Remote side effects are not covered" in augmented


def test_mcp_tool_detection_ignores_malformed_schemas():
    malformed = [None, {}, {"function": "bad"}, {"function": {"name": 7}}]
    assert not has_discovered_mcp_tools(malformed)
