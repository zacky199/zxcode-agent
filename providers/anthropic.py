import json
from typing import AsyncGenerator, Dict, Any, List

import httpx

from .base import BaseProvider, AgentResponse


class AnthropicProvider(BaseProvider):
    """Provider for the Anthropic Messages API with native tool support."""

    def _get_headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    @staticmethod
    def _convert_tools(tools: list) -> list:
        """Convert OpenAI-style tool schemas to Anthropic tool schemas."""
        converted = []
        for tool in tools:
            converted.append({
                "name": tool.get("function", {}).get("name", tool.get("name", "")),
                "description": tool.get("function", {}).get("description", tool.get("description", "")),
                "input_schema": tool.get("function", {}).get("parameters", tool.get("parameters", {"type": "object", "properties": {}})),
            })
        return converted

    async def complete_async(self, system: str, messages: list,
                             tools: list = None) -> AgentResponse:
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 8192,
            "system": system,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = self._convert_tools(tools)

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(self.base_url, headers=self._get_headers(), json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()

        text_parts = []
        tool_calls = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "arguments": block.get("input", {}) or {},
                })
        return AgentResponse(text="\n".join(text_parts), tool_calls=tool_calls, raw=data)

    async def stream_complete_async(self, system: str, messages: list,
                                    tools: list = None) -> AsyncGenerator[Dict[str, Any], None]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 8192,
            "system": system,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = self._convert_tools(tools)

        tool_acc: Dict[str, Dict[str, Any]] = {}
        text_parts: List[str] = []

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST", self.base_url, headers=self._get_headers(), json=payload
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    yield {"type": "error", "content": f"HTTP {response.status_code}: {body[:500]}"}
                    return

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue

                    evt_type = data.get("type")
                    if evt_type == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            tool_acc[data.get("index", 0)] = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "arguments": "",
                            }
                    elif evt_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            text_parts.append(text)
                            yield {"type": "text", "content": text}
                        elif delta.get("type") == "input_json_delta":
                            idx = data.get("index", 0)
                            if idx in tool_acc:
                                tool_acc[idx]["arguments"] += delta.get("partial_json", "")
                    elif evt_type == "message_stop":
                        break

        if tool_acc:
            for idx in sorted(tool_acc.keys()):
                slot = tool_acc[idx]
                try:
                    args = json.loads(slot["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool_call", "content": {
                    "id": slot.get("id") or f"call_{idx}",
                    "name": slot.get("name", ""),
                    "arguments": args,
                }}

        yield {"type": "done", "content": "".join(text_parts)}
