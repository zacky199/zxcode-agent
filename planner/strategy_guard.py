"""Deterministic guards against repeated adaptive replanning strategies."""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{1,48}")
_VOLATILE_RE = re.compile(
    r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}[ t]\d{1,2}:\d{2}:\d{2})\b"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    text = _VOLATILE_RE.sub("<volatile>", str(text)[:8000].lower())
    return " ".join(text.split())


def failure_fingerprint(*, tool_name: str = "", outcome: str = "", error_text: str = "",
                        verification_text: str = "") -> str:
    """Return a stable hash of bounded failure metadata; never store raw evidence."""
    normalized = "\n".join(_normalize(part) for part in
                         (tool_name, outcome, error_text, verification_text))
    return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()[:24]


def similarity(left: str, right: str) -> float:
    """Conservative textual similarity (sequence + token overlap)."""
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    tokens_a, tokens_b = set(_TOKEN_RE.findall(a)), set(_TOKEN_RE.findall(b))
    overlap = len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))
    sequence = SequenceMatcher(None, a, b, autojunk=False).ratio()
    return max(sequence, overlap)


def is_strategy_diverse(failed_strategy: str, proposed_steps: list[str], *, threshold: float = 0.88) -> bool:
    """Reject a proposed decomposition that repeats the failed strategy nearly verbatim."""
    if not isinstance(proposed_steps, list) or not proposed_steps:
        return False
    if not 0.5 <= threshold <= 1.0:
        threshold = 0.88
    return all(similarity(failed_strategy, step) < threshold for step in proposed_steps)
