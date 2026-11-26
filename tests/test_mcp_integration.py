"""Agent-loop MCP integration guidance tests."""
from core.mcp_integration import (
    augment_system_prompt, has_discovered_mcp_tools,
    select_relevant_mcp_schemas, filter_tool_schemas_for_task,
)


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


def test_routing_selects_relevant_mcp_tools_deterministically_and_limits_count():
    local = {"type": "function", "function": {"name": "read_file"}}
    weather = {"type": "function", "function": {
        "name": "mcp__weather__forecast", "description": "Get forecast and current weather conditions"}}
    github = {"type": "function", "function": {
        "name": "mcp__github__search_issues", "description": "Search issues and pull requests in repositories"}}
    irrelevant = {"type": "function", "function": {
        "name": "mcp__calendar__events", "description": "List and create calendar events"}}
    schemas = [local, weather, github, irrelevant]
    chosen = select_relevant_mcp_schemas("weather forecast in Jakarta", schemas)
    assert chosen == [weather]
    filtered = filter_tool_schemas_for_task("search github issues for bug", schemas)
    assert filtered[0] == local
    assert filtered[1:] == [github]
    assert filter_tool_schemas_for_task("unrelated request", schemas) == [local]
    assert select_relevant_mcp_schemas("weather forecast", schemas, max_tools=1) == [weather]


def test_mcp_routing_handles_empty_and_malformed_inputs():
    assert select_relevant_mcp_schemas("", []) == []
    assert select_relevant_mcp_schemas("weather", [None, {}, {"function": "bad"}]) == []
    assert filter_tool_schemas_for_task("", [None, {"function": {"name": "read_file"}}]) == [None, {"function": {"name": "read_file"}}]
