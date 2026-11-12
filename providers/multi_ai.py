"""
Multi-AI Router.

Allows a single agent to fan out prompts to several models/providers at once,
then either pick the best answer, vote, or merge the results. Also supports
role-based routing (planner uses a cheap model, executor uses the main model,
reviewer uses a third) which mimics professional agent setups such as
opencode / Claude Code multi-agent workflows.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import AgentResponse


@dataclass
class AIBackend:
    """A named provider instance usable by the router."""
    role: str                  # 'main', 'planner', 'reviewer', 'coder', ...
    provider: Any
    weight: float = 1.0

    @property
    def name(self) -> str:
        return getattr(self.provider, "model", type(self.provider).__name__)


@dataclass
class MultiAIResult:
    """Outcome of a multi-AI fan-out."""
    role: str
    model: str
    text: str
    score: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.text


class MultiAIRouter:
    """Fans out a prompt to several backends and merges the answers.

    Modes:
      * ``best``   - return the highest scoring response (default)
      * ``vote``   - return the most frequent answer when asked a yes/no question
      * ``merge``  - concatenate all answers with separators (for ideation)
      * ``first``  - return the first backend that responds
    """

    def __init__(self, backends: Optional[List[AIBackend]] = None):
        self.backends: List[AIBackend] = backends or []

    def add_backend(self, role: str, provider: Any, weight: float = 1.0) -> None:
        self.backends.append(AIBackend(role=role, provider=provider, weight=weight))

    def backends_for_role(self, role: str) -> List[AIBackend]:
        return [b for b in self.backends if b.role == role] or self.backends

    @staticmethod
    def _score(response: AgentResponse) -> float:
        text = response.text or ""
        score = min(len(text) / 500.0, 3.0)          # prefer substantive answers
        if "error" in text.lower()[:200]:
            score -= 2.0
        score += (0.5 if response.tool_calls else 0.0)
        return max(score, 0.0)

    async def _run_one(self, backend: AIBackend, system: str,
                       messages: List[Dict[str, str]]) -> MultiAIResult:
        try:
            response = await backend.provider.complete_async(system, messages)
        except Exception as e:
            response = AgentResponse(text=f"[MULTI-AI ERROR] {e}", tool_calls=[])
        return MultiAIResult(
            role=backend.role,
            model=backend.name,
            text=response.text or "",
            score=self._score(response) * backend.weight,
            raw={"provider": type(backend.provider).__name__},
        )

    async def ask(self, system: str, messages: List[Dict[str, str]],
                  mode: str = "best", role: str = "main") -> MultiAIResult:
        """Run the prompt across all backends (or a single role) in parallel."""
        backends = self.backends_for_role(role)
        if not backends:
            raise RuntimeError("No AI backends registered for multi-AI routing.")

        results = await asyncio.gather(*[self._run_one(b, system, messages) for b in backends])

        if mode == "first":
            return results[0]
        if mode == "merge":
            merged = MultiAIResult(role=role, model="+".join(r.model for r in results),
                                   text="\n\n---\n\n".join(r.text for r in results))
            return merged
        if mode == "vote":
            votes: Dict[str, int] = {}
            for r in results:
                key = r.text.strip().lower()[:80]
                votes[key] = votes.get(key, 0) + 1
            best_key = max(votes, key=votes.get)
            for r in results:
                if r.text.strip().lower()[:80] == best_key:
                    return r

        results.sort(key=lambda r: r.score, reverse=True)
        return results[0]

    async def ask_simple(self, prompt: str, system: str = "",
                         mode: str = "best", role: str = "main") -> str:
        """Convenience wrapper that returns a plain string."""
        messages = [{"role": "user", "content": prompt}]
        result = await self.ask(system, messages, mode=mode, role=role)
        return result.text


class RoleRouter:
    """Route each stage of the agent pipeline to a dedicated model.

    Example: planner -> 'claude-haiku' (fast/cheap),
             coder   -> 'deepseek-v4' (main),
             reviewer-> 'gemini-flash' (second opinion).
    """

    def __init__(self, backends: Dict[str, Any]):
        self.backends = backends  # {role: provider}

    def for_role(self, role: str, fallback: Any):
        return self.backends.get(role, fallback)
