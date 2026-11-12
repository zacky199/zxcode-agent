"""Bounded per-node verification plans executed through the configured sandbox."""
from __future__ import annotations

from typing import Any, Dict, List

from execution.test_runner import TesterRunner


class NodeVerificationPlan:
    MAX_COMMANDS = 5
    MAX_COMMAND_LENGTH = 500
    MAX_OUTPUT = 3000
    TIMEOUT_SECONDS = 120

    @classmethod
    def normalize(cls, commands: Any) -> List[str]:
        if not isinstance(commands, list) or len(commands) > cls.MAX_COMMANDS:
            raise ValueError("verification_commands must be a list of at most five commands")
        normalized = []
        for command in commands:
            if not isinstance(command, str) or not command.strip() or len(command) > cls.MAX_COMMAND_LENGTH:
                raise ValueError("each verification command must be a non-empty string up to 500 characters")
            normalized.append(command.strip())
        return normalized

    @classmethod
    def execute(cls, sandbox, commands: Any) -> Dict[str, Any]:
        plan = cls.normalize(commands)
        if not plan:
            return {"status": "not_configured", "results": [],
                    "reason": "no node-specific verification commands were declared"}
        results = TesterRunner(sandbox).run(plan, timeout=cls.TIMEOUT_SECONDS)
        bounded = []
        for item in results.get("results", []):
            bounded.append({
                "command": str(item.get("command", ""))[:cls.MAX_COMMAND_LENGTH],
                "status": str(item.get("status", "unknown"))[:40],
                "returncode": int(item.get("returncode", -1)),
                "stdout": str(item.get("stdout", ""))[:cls.MAX_OUTPUT],
                "stderr": str(item.get("stderr", ""))[:cls.MAX_OUTPUT],
            })
        return {"status": results.get("status", "failed"), "results": bounded,
                "reason": "node verification commands executed through configured sandbox"}
