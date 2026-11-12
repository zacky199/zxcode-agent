"""Decide whether a request is worth a separate planning call."""
import re

_GREETING = re.compile(
    r"^\s*(?:hi|hai|halo|hello|hey|hola|pagi|siang|sore|malam|selamat\s+\w+|"
    r"thanks?|thx|terima\s*kasih|makasih|ok(?:e|ay)?|sip|siap|test|tes|ping|yes|ya|no|tidak)\W*$",
    re.IGNORECASE,
)
_QUESTION_START = re.compile(
    r"^\s*(?:apa|apakah|siapa|kenapa|mengapa|bagaimana|gimana|kapan|dimana|di\s+mana|berapa|"
    r"what|why|how|when|where|who|which|is|are|can|does|explain|jelaskan|ceritakan)\b",
    re.IGNORECASE,
)
_ACTION = re.compile(
    r"\b(?:buat(?:kan)?|bikin|tambah(?:kan)?|ubah|ganti|perbaiki|fix|hapus|delete|remove|install|"
    r"jalankan|run|create|add|implement|refactor|write|edit|update|build|deploy|migrate|rename|"
    r"convert|generate|setup|set\s*up|tulis|pindahkan|move|optimi[sz]e)\b",
    re.IGNORECASE,
)

SHORT_CHAT_LIMIT = 160


def should_plan(prompt: str, mode: str = "auto") -> bool:
    """Return True when a planner call is worthwhile.

    ``mode`` is ``"always"``, ``"never"`` or ``"auto"``. In auto mode only
    greetings and short questions that do not ask for any change skip planning;
    everything else (including terse commands such as "fix login") is planned.
    """
    if mode == "always":
        return True
    if mode == "never":
        return False
    text = (prompt or "").strip()
    if not text:
        return False
    if _GREETING.match(text):
        return False
    if len(text) <= SHORT_CHAT_LIMIT and not _ACTION.search(text):
        if text.endswith("?") or _QUESTION_START.match(text):
            return False
    return True
