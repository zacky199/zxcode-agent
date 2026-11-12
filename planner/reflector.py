import re
from typing import Tuple


class TaskReflector:
    """Evaluates execution logs and detects genuine failures (self-reflection)."""

    # Patterns that almost always indicate a hard failure.
    HARD_ERROR_PATTERNS = [
        r"Traceback \(most recent call last\):",
        r"(?i)syntaxerror:",
        r"(?i)modulenotfounderror:",
        r"(?i)importerror:",
        r"(?i)attributeerror:",
        r"(?i)typeerror:",
        r"(?i)nameerror:",
        r"(?i)indentationerror:",
        r"(?i)indexerror:",
        r"(?i)keyerror:",
        r"(?i)command not found",
        r"(?i)fatal error",
        r"(?i)cannot find module",
        r"(?i)no such file or directory",
        r"(?i)compilation failed",
        r"(?i)build failed",
        r"(?i)error: could not",
        r"(?i)failed to (install|build|compile|run|resolve)",
        r"(?i)npm err!",
        r"(?i)exception in thread",
        r"(?i)segmentation fault",
    ]

    # Patterns that are merely warnings or benign mentions.
    SOFT_PATTERNS = [
        r"(?i)deprecationwarning",
        r"(?i)pendingdeprecationwarning",
        r"(?i)futurewarning",
        r"(?i)userwarning",
        r"(?i)warn:",
        r"(?i)notice:",
        r"(?i)npm warn",
    ]

    @staticmethod
    def evaluate(execution_log: str) -> Tuple[bool, str]:
        """Analyze an execution log.

        Returns ``(is_success, error_message)``.
        """
        if not execution_log or not execution_log.strip():
            return True, "No output; treated as success."

        log = execution_log
        for pattern in TaskReflector.HARD_ERROR_PATTERNS:
            match = re.search(pattern, log)
            if match:
                snippet = TaskReflector._snippet(log, match.start())
                return False, f"Detected failure: {snippet}"

        # If it contains 'Exit Code N' with N != 0, treat as failure.
        exit_matches = re.findall(r"(?i)exit\s+code\s+(-?\d+)", log)
        if any(int(code) != 0 for code in exit_matches):
            return False, "Command exited with a non-zero exit code."

        # Neutral mentions of the word error inside a success context (e.g.
        # "handled error gracefully") are ignored.
        if re.search(r"(?i)ERROR:", log) and not TaskReflector._is_benign(log):
            start = log.find("ERROR:")
            return False, TaskReflector._snippet(log, start)

        return True, "Execution completed without hard failures."

    @staticmethod
    def _is_benign(log: str) -> bool:
        benign_contexts = ["no error", "without error", "without any error",
                           "0 error", "successfully", "handled", "ignored",
                           "expected error", "error handling"]
        for ctx in benign_contexts:
            if ctx in log.lower():
                return True
        return False

    @staticmethod
    def _snippet(log: str, start: int, width: int = 240) -> str:
        raw = log[start:start + width].strip()
        return re.sub(r"\s+", " ", raw)
