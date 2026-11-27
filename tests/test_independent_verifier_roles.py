import asyncio
from types import SimpleNamespace
from core.roles import RoleOrchestrator, ROLE_CONTRACTS


class Provider:
    def __init__(self, model):
        self.model = model
    async def complete_async(self, system, messages):
        return SimpleNamespace(text='{"verdict":"verified","reason":"evidence","criteria":[{"criterion":"works","passed":true,"evidence":"test"}]}')

class Router:
    def __init__(self, provider):
        self.provider = provider
    def for_role(self, role, primary):
        return self.provider


def test_verifier_role_contract_exists_and_returns_distinct_model_metadata():
    primary, verifier = Provider("model-a"), Provider("model-b")
    roles = RoleOrchestrator(primary, Router(verifier))
    assert "verifier" in ROLE_CONTRACTS
    assert roles.has_independent_provider("verifier")
    result = asyncio.run(roles.analyze("verifier", "verify criteria", "diff/test evidence"))
    assert result["model"] == "model-b"
    assert "verdict" in result["text"]


def test_verifier_role_is_not_called_independent_without_distinct_named_model():
    primary, unknown = Provider("model-a"), Provider(None)
    roles = RoleOrchestrator(primary, Router(unknown))
    assert not roles.has_independent_provider("verifier")
    same = RoleOrchestrator(primary, Router(Provider("model-a")))
    assert not same.has_independent_provider("verifier")


def test_independent_verifier_orchestration_marks_missing_provider_inconclusive_or_unconfigured():
    from execution.independent_verifier import run_independent_verifier
    primary = Provider("same-model")
    roles = RoleOrchestrator(primary, Router(Provider("same-model")))
    result = asyncio.run(run_independent_verifier(roles, objective="goal", node_task="task",
        coder_report="done", command_evidence={"status":"passed"}, diff_evidence="patch"))
    assert result["status"] == "not_configured"


def test_independent_verifier_orchestration_returns_verdict_and_model():
    from execution.independent_verifier import run_independent_verifier
    primary, secondary = Provider("coder-model"), Provider("verifier-model")
    roles = RoleOrchestrator(primary, Router(secondary))
    result = asyncio.run(run_independent_verifier(roles, objective="goal", node_task="task",
        coder_report="done", command_evidence={"status":"passed"}, diff_evidence="patch"))
    assert result["status"] == "verified"
    assert result["model"] == "verifier-model"
