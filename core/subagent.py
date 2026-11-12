"""Sub-agent execution for parallel task delegation.

A sub-agent runs its own bounded ReAct loop with a dedicated, small context
window. This keeps long-running agents from blowing up their context budget
with busywork that can be delegated (e.g. "scan module X and summarize").

The manager gives every sub-agent:

* a **fresh message buffer** (no leakage from the parent loop),
* a **bounded tool budget** (max tool calls before forced return),
* **parallel execution** via ``asyncio.gather``,
* a **structured result** (final text + tool call count) the parent can
  feed back into its own context.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils import setup_logger

log = setup_logger("zx.subagent")


class SubAgentResult:
    def __init__(self, name: str, text: str, tool_calls: int = 0,
                 ok: bool = True, error: str = ""):
        self.name = name
        self.text = text
        self.tool_calls = tool_calls
        self.ok = ok
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "text": self.text,
            "tool_calls": self.tool_calls,
            "ok": self.ok,
            "error": self.error,
        }


class SubAgent:
    """A single delegated task with its own ReAct loop and shared process access."""

    SYSTEM_PROMPT = (
        "You are a focused sub-agent. Complete exactly the assigned task inside "
        "the workspace using the available tools. Be concise. Do not ask the "
        "parent agent questions; use tools to discover what you need. "
        "Report a short final summary when done."
    )

    def __init__(self, name: str, task: str, workspace: Path,
                 tool_engine: Any, provider: Any,
                 max_tool_calls: int = 8,
                 tools_whitelist: Optional[List[str]] = None,
                 confirm_callback: Callable[[str], bool] = None,
                 checkpoint_enabled: bool = True):
        self.name = name
        self.task = task
        self.workspace = workspace
        self.tool_engine = tool_engine
        self.provider = provider
        self.max_tool_calls = max_tool_calls
        self.tools_whitelist = tools_whitelist
        self.confirm_callback = confirm_callback
        self.checkpoint_enabled = checkpoint_enabled

    async def run(self) -> SubAgentResult:
        journal = self.tool_engine.create_mutation_journal(
            owner_id=f"subagent-{uuid.uuid4().hex}",
            checkpoint_enabled=self.checkpoint_enabled,
        )
        try:
            result = await self._run_with_journal(journal)
            summary = journal.close()
            if summary:
                result.text = (result.text + "\n\n" + summary).strip()
            return result
        finally:
            journal.close()

    async def _run_with_journal(self, journal) -> SubAgentResult:
        log.info("sub-agent start", extra={"fields": {"name": self.name,
                                                      "task_len": len(self.task)}})
        messages = [{"role": "user", "content": self.task}]
        use_tools = getattr(self.provider, "supports_tools", True)
        schemas = self.tool_engine.get_tool_schemas() if use_tools else None
        if self.tools_whitelist:
            schemas = [s for s in (schemas or [])
                       if s.get("function", {}).get("name") in self.tools_whitelist]

        text_parts: List[str] = []
        tool_calls_count = 0

        for iteration in range(self.max_tool_calls + 1):
            tool_calls: List[Dict[str, Any]] = []
            assistant_text = ""
            try:
                async for chunk in self.provider.stream_complete_async(
                    self.SYSTEM_PROMPT, messages, tools=schemas
                ):
                    ctype = chunk.get("type")
                    if ctype == "text":
                        assistant_text += chunk.get("content", "")
                        text_parts.append(chunk.get("content", ""))
                    elif ctype == "tool_call":
                        tool_calls.append(chunk.get("content", {}))
                    elif ctype == "error":
                        return SubAgentResult(
                            self.name, "".join(text_parts),
                            tool_calls_count, ok=False, error=str(chunk.get("content", "")))
            except Exception as e:
                return SubAgentResult(
                    self.name, "".join(text_parts), tool_calls_count,
                    ok=False, error=str(e))

            messages.append({"role": "assistant", "content": assistant_text or None})
            if not tool_calls:
                break

            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("arguments", {}) or {}
                if self.tools_whitelist and name not in self.tools_whitelist:
                    messages.append({"role": "tool",
                                     "tool_call_id": tc.get("id", f"call_{iteration}"),
                                     "content": "Tool not allowed in this sub-agent."})
                    continue
                result = await self.tool_engine.execute_tool(name, args, journal=journal)
                tool_calls_count += 1
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", f"call_{iteration}_{tool_calls_count}"),
                    "content": (result.get("output") or result.get("status", ""))[:4000],
                })

        final_text = "".join(text_parts).strip() or "(sub-agent produced no text)"
        log.info("sub-agent done", extra={"fields": {"name": self.name,
                                                     "tool_calls": tool_calls_count,
                                                     "ok": True}})
        return SubAgentResult(self.name, final_text, tool_calls_count)


class SubAgentManager:
    """Runs multiple sub-agents in parallel and collects structured results."""

    def __init__(self, workspace: Path, tool_engine: Any, provider: Any,
                 confirm_callback: Callable[[str], bool] = None,
                 max_concurrent: int = 4,
                 checkpoint_enabled: bool = True):
        self.workspace = workspace
        self.tool_engine = tool_engine
        self.provider = provider
        self.confirm_callback = confirm_callback
        self.max_concurrent = max_concurrent
        self.checkpoint_enabled = checkpoint_enabled

    async def run_many(self, tasks: List[Dict[str, Any]],
                       parallel: bool = True) -> List[SubAgentResult]:
        """Run delegated tasks concurrently or in deterministic sequence."""
        log.info("delegating tasks", extra={"fields": {"count": len(tasks)}})
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def worker(spec: Dict[str, Any]) -> SubAgentResult:
            async with semaphore:
                agent = SubAgent(
                    name=spec.get("name", "sub"),
                    task=spec.get("task", ""),
                    workspace=self.workspace,
                    tool_engine=self.tool_engine,
                    provider=self.provider,
                    tools_whitelist=spec.get("tools"),
                    confirm_callback=self.confirm_callback,
                    checkpoint_enabled=self.checkpoint_enabled,
                )
                return await agent.run()

        if not parallel:
            results = []
            for task in tasks:
                results.append(await worker(task))
            return results
        return await asyncio.gather(*(worker(t) for t in tasks))

    def format_results(self, results: List[SubAgentResult]) -> str:
        lines = []
        for r in results:
            status = "OK" if r.ok else f"ERROR: {r.error}"
            lines.append(f"### Sub-agent [{r.name}] ({status}, {r.tool_calls} tool calls)\n{r.text}")
        return "\n\n".join(lines)
