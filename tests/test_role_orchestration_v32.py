"""Contract tests for ZX Code v3.2 role orchestration."""
import pytest

from core import AgentConfig, ZXAgentPro
from providers.multi_ai import RoleRouter


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.tool_calls = []
        self.raw = {}


class RoleProvider:
    supports_tools = True

    def __init__(self, model, response="role output"):
        self.model = model
        self.response = response
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete_async(self, system, messages, tools=None):
        self.complete_calls += 1
        if self.model == "planner":
            return FakeResponse('{"objective":"ship feature","steps":[{"description":"implement","depends_on":[]}]}')
        return FakeResponse(self.response)

    async def stream_complete_async(self, system, messages, tools=None):
        self.stream_calls += 1
        yield {"type": "text", "content": "implementation complete"}
        yield {"type": "done", "content": ""}


class FakeJournal:
    has_successful_mutations = True

    def close(self):
        return "Modified: src/example.py (created)"


@pytest.mark.asyncio
async def test_all_configured_roles_are_routed_to_their_stages(tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    primary = RoleProvider("primary")
    providers = {
        "architect": RoleProvider("architect", "Use a small service boundary."),
        "planner": RoleProvider("planner"),
        "coder": RoleProvider("coder"),
        "tester": RoleProvider("tester", "Run unit tests and type checks."),
        "reviewer": RoleProvider("reviewer", "No correctness blockers found."),
        "security_auditor": RoleProvider("security", "No critical security findings."),
    }
    agent = ZXAgentPro(
        primary, workspace,
        role_router=RoleRouter(providers),
        config=AgentConfig(store_tool_memory=False),
    )
    monkeypatch.setattr(agent.tool_engine, "create_mutation_journal", lambda **kwargs: FakeJournal())

    events = [event async for event in agent.chat_stream("implement a feature")]
    role_events = [e["content"]["role"] for e in events if e["type"] == "role_result"]

    assert events[-1]["type"] == "done"
    assert set(role_events) == {"architect", "tester", "reviewer", "security_auditor"}
    assert providers["architect"].complete_calls == 1
    assert providers["planner"].complete_calls == 1
    assert providers["tester"].complete_calls == 1
    assert providers["coder"].stream_calls == 1
    assert providers["reviewer"].complete_calls == 1
    assert providers["security_auditor"].complete_calls == 1
    assert "Security Auditor" in events[-1]["content"]


@pytest.mark.asyncio
async def test_same_model_fallback_does_not_claim_independent_review(tmp_path, monkeypatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    primary = RoleProvider("same-model")
    reviewer = RoleProvider("same-model")
    agent = ZXAgentPro(
        primary, workspace,
        role_router=RoleRouter({"reviewer": reviewer}),
        config=AgentConfig(store_tool_memory=False),
    )
    monkeypatch.setattr(agent.tool_engine, "create_mutation_journal", lambda **kwargs: FakeJournal())

    events = [event async for event in agent.chat_stream("implement a feature")]
    roles = [e["content"]["role"] for e in events if e["type"] == "role_result"]

    assert "reviewer" not in roles
    assert reviewer.complete_calls == 0
