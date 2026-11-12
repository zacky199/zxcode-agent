"""Deterministic execution-level acceptance checks for DAG nodes.

This contract proves that the agent loop terminated cleanly and its mutation
journal closed. It does not claim semantic correctness; project tests and review
gates remain separate verification layers.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict


@dataclass(frozen=True)
class AcceptanceResult:
    accepted: bool
    checks: Dict[str, bool]
    evidence: str

    def to_dict(self) -> dict:
        return asdict(self)


class NodeAcceptanceContract:
    @staticmethod
    def evaluate(*, result: str, fatal: bool, journal_closed: bool,
                 verification: dict | None = None) -> AcceptanceResult:
        verification = verification if isinstance(verification, dict) else {"status": "not_configured"}
        verification_required = verification.get("status") != "not_configured"
        verification_passed = (not verification_required or verification.get("status") == "passed")
        checks = {
            "react_loop_terminated_cleanly": not bool(fatal),
            "final_response_present": bool(isinstance(result, str) and result.strip()),
            "mutation_journal_closed": bool(journal_closed),
            "node_verification_passed": verification_passed,
        }
        accepted = all(checks.values())
        failed = [name for name, passed in checks.items() if not passed]
        evidence = ("Execution contract satisfied." if accepted else
                    "Execution contract failed: " + ", ".join(failed))
        return AcceptanceResult(accepted=accepted, checks=checks, evidence=evidence)
