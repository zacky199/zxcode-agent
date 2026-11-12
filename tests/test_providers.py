"""Tests for provider layer: normalization and registry factory."""
import pytest

from providers.registry import get_provider, PROVIDERS, provider_names
from providers.openai_compat import OpenAICompatProvider
from providers.multi_ai import RoleRouter, MultiAIRouter


def test_registry_has_expected_providers():
    for name in ("claude", "deepseek", "nararouter", "lapakrouter", "ollama"):
        assert name in PROVIDERS
    assert provider_names()  # non-empty string


def test_get_provider_openai_compat():
    p = get_provider("deepseek", "dummy", model="x", base_url="http://localhost:1")
    assert isinstance(p, OpenAICompatProvider)
    assert p.model == "x"


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError):
        get_provider("does-not-exist", "k")


def test_normalize_tool_calls():
    raw = {
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"file_path": "a.txt"}'},
        }]
    }
    calls = OpenAICompatProvider._normalize_tool_calls(raw)
    assert calls[0]["name"] == "read_file"
    assert calls[0]["arguments"] == {"file_path": "a.txt"}


def test_normalize_bad_json_arguments():
    raw = {"tool_calls": [{"function": {"name": "x", "arguments": "not json"}}]}
    calls = OpenAICompatProvider._normalize_tool_calls(raw)
    assert calls[0]["arguments"] == {}


class FakeProvider:
    model = "m"
    supports_tools = True


def test_role_router_fallback():
    main = FakeProvider()
    router = RoleRouter({"main": main, "coder": main})
    assert router.for_role("planner", main) is main
    # Unknown role falls back to main.
    assert router.for_role("reviewer", main) is main


def test_multi_ai_router_first():
    class P:
        def __init__(self, n): self.model = n; self.calls = 0
        async def complete_async(self, s, m, tools=None):
            self.calls += 1
            return SimpleResponse("answer")
    a, b = P("a"), P("b")
    from providers.multi_ai import AIBackend
    router = MultiAIRouter([AIBackend("main", a), AIBackend("main", b)])
    assert len(router.backends) == 2
    assert router.backends_for_role("main")  # both backends selected


class SimpleResponse:
    def __init__(self, text):
        self.text = text
        self.tool_calls = []
        self.raw = {}
