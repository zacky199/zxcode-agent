"""Shared fixtures and mock providers for the ZX Agent test suite."""
import asyncio
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def workspace(tmp_path):
    return tmp_path


@pytest.fixture()
def mock_provider():
    """A simple deterministic streaming provider for agent-loop tests."""

    class MockProvider:
        model = "mock-model"
        supports_tools = True

        def __init__(self):
            self.stream_calls = 0
            self.complete_calls = 0
            self.tool_script = []
            self.text_script = []

        async def complete_async(self, system, messages, tools=None):
            self.complete_calls += 1
            return SimpleResponse(
                '{"objective":"mock task","steps":[{"description":"step 1","depends_on":[]}]}'
            )

        async def stream_complete_async(self, system, messages, tools=None):
            self.stream_calls += 1
            for event in self.tool_script:
                yield event
            for event in self.text_script:
                yield event
            yield {"type": "done", "content": ""}

    return MockProvider()


class SimpleResponse:
    def __init__(self, text):
        self.text = text
        self.tool_calls = []
        self.raw = {}
