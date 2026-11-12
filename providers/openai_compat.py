import json
from typing import List, Dict, Any, AsyncGenerator

import httpx

from .base import BaseProvider, AgentResponse


class OpenAICompatProvider(BaseProvider):
    """
    Provider for any OpenAI-compatible ``/chat/completions`` endpoint.

    Supports strict routing routers (LapakVIP, NaraRouter, 9Router local),
    DeepSeek, OpenAI, Groq, Gemini (OpenAI shim), Ollama and MiniMax.
    A minimal payload is used by default to maximize compatibility; the
    ``tools`` field is only attached when the caller supplies a schema.
    """

    def __init__(self, api_key: str, base_url: str, model: str,
                 timeout: float = 300.0, reasoning: bool = False):
        super().__init__(api_key, base_url, model)
        self.timeout = timeout
        self.reasoning = reasoning

    def _build_headers(self) -> Dict[str, str]:
        key = self.api_key if self.api_key else "dummy"
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def _build_payload(self, system: str, messages: List[Dict[str, str]],
                       tools: list = None, stream: bool = False) -> Dict[str, Any]:
        formatted = [{"role": "system", "content": system}] + list(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": formatted,
        }
        if self.reasoning:
            payload["reasoning_effort"] = "medium"
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream"] = True
            payload.setdefault("stream_options", {"include_usage": True})
        return payload

    @staticmethod
    def _normalize_tool_calls(message: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Convert OpenAI-style tool_calls into internal shape."""
        calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            calls.append({
                "id": tc.get("id") or "",
                "name": fn.get("name") or "",
                "arguments": arguments,
            })
        return calls

    async def complete_async(self, system: str, messages: list,
                             tools: list = None) -> AgentResponse:
        payload = self._build_payload(system, messages, tools)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.base_url, headers=self._build_headers(), json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()

        message = (data.get("choices") or [{}])[0].get("message", {})
        text = message.get("content") or ""
        tool_calls = self._normalize_tool_calls(message)
        return AgentResponse(text=text, tool_calls=tool_calls, raw=data)

    async def stream_complete_async(self, system: str, messages: list,
                                    tools: list = None) -> AsyncGenerator[Dict[str, Any], None]:
        payload = self._build_payload(system, messages, tools, stream=True)
        tool_acc: Dict[str, Dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", self.base_url, headers=self._build_headers(), json=payload
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    yield {"type": "error", "content": f"HTTP {response.status_code}: {body[:500]}"}
                    return

                text_parts: List[str] = []
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if data.get("error"):
                        yield {"type": "error", "content": str(data["error"])}
                        break

                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}

                    if delta.get("content"):
                        text_parts.append(delta["content"])
                        yield {"type": "text", "content": delta["content"]}

                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

        if tool_acc:
            for idx in sorted(tool_acc.keys()):
                slot = tool_acc[idx]
                try:
                    args = json.loads(slot["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield {
                    "type": "tool_call",
                    "content": {
                        "id": slot["id"] or f"call_{idx}",
                        "name": slot["name"],
                        "arguments": args,
                    },
                }

        yield {"type": "done", "content": "".join(text_parts)}
