"""Small text helpers shared by the agent loop and tools."""


def truncate_middle(text, limit: int = 12000, head_ratio: float = 0.6) -> str:
    """Shorten ``text`` to about ``limit`` characters, keeping its head and tail.

    Build logs and test output usually carry the useful part at the start (what
    ran) and at the end (what failed), so the middle is what gets dropped.
    """
    if text is None:
        return ""
    text = str(text)
    if limit <= 0 or len(text) <= limit:
        return text
    head = max(1, int(limit * head_ratio))
    tail = max(0, limit - head)
    omitted = len(text) - head - tail
    marker = (f"\n... [{omitted} characters omitted from the middle; "
              "narrow the command or read the file in line ranges] ...\n")
    return text[:head] + marker + (text[-tail:] if tail else "")
