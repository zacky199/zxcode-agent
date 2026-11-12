from abc import ABC, abstractmethod
from typing import List, Dict, Any, AsyncGenerator

class AgentResponse:
    """Standardized response object shared across all providers."""
    def __init__(self, text: str, tool_calls: List[Dict[str, Any]], raw: Dict[str, Any] = None):
        self.text = text
        self.tool_calls = tool_calls
        self.raw = raw or {}

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def content(self) -> str:
        return self.text


class BaseProvider(ABC):
    """Abstract contract every model provider must satisfy.

    Providers stream events of the following shapes:

    * ``{"type": "text", "content": "<delta>"}``       - assistant text token
    * ``{"type": "tool_call", "content": {name,id,...}}`` - a completed tool call
    * ``{"type": "done", "content": "<final text>"}``    - stream finished
    * ``{"type": "error", "content": "<message>"}``      - a failure happened

    Two execution modes exist: ``complete_async`` (single-shot, used by the
    planner) and ``stream_complete_async`` (token streaming, used by the agent
    loop). Both accept an optional OpenAI-style ``tools`` schema list.
    """

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @property
    def name(self) -> str:
        return type(self).__name__

    @property
    def supports_tools(self) -> bool:
        return True

    @abstractmethod
    async def complete_async(self, system: str, messages: list, tools: list = None) -> AgentResponse:
        """Non-streaming completion. Returns a full AgentResponse."""

    @abstractmethod
    async def stream_complete_async(self, system: str, messages: list, tools: list = None) -> AsyncGenerator[Dict[str, Any], None]:
        """Streaming completion. Yields event dictionaries (see class docstring)."""
        raise StopAsyncIteration

    def tool_calls_from_raw(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalize provider-specific tool call payloads into the internal shape:
        ``{"id", "name", "arguments": dict}``."""
        return []
