"""Strict, evidence-bounded parser for independent semantic verification."""
from __future__ import annotations
import json
import re
from typing import Any


MAX_CRITERIA = 8
MAX_TEXT = 1000


def parse_verifier_result(raw: str) -> dict[str, Any]:
    """Parse a verifier verdict; malformed output remains inconclusive, never a pass."""
    candidate = (raw or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.I)
    try:
        data = json.loads(candidate)
    except (ValueError, TypeError):
        return {"status": "inconclusive", "reason": "verifier returned invalid JSON", "criteria": []}
    if not isinstance(data, dict) or data.get("verdict") not in {"verified", "failed", "inconclusive"}:
        return {"status": "inconclusive", "reason": "verifier output lacks a valid verdict", "criteria": []}
    criteria = data.get("criteria")
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= MAX_CRITERIA:
        return {"status": "inconclusive", "reason": "verifier criteria list is missing or out of bounds", "criteria": []}
    bounded = []
    for entry in criteria:
        if not isinstance(entry, dict) or not isinstance(entry.get("passed"), bool):
            return {"status": "inconclusive", "reason": "verifier criterion has invalid shape", "criteria": []}
        bounded.append({"criterion": str(entry.get("criterion", ""))[:MAX_TEXT],
                       "passed": entry["passed"],
                       "evidence": str(entry.get("evidence", ""))[:MAX_TEXT]})
    verdict = data["verdict"]
    if verdict == "verified" and not all(item["passed"] for item in bounded):
        verdict = "failed"
    if verdict == "failed" and all(item["passed"] for item in bounded):
        return {"status": "inconclusive", "reason": "failed verdict conflicts with all criteria passing", "criteria": bounded}
    return {"status": verdict, "reason": str(data.get("reason", ""))[:MAX_TEXT], "criteria": bounded}



async def run_independent_verifier(roles: Any, *, objective: str, node_task: str,
                                  coder_report: str, command_evidence: dict,
                                  diff_evidence: str) -> dict[str, Any]:
    """Run the distinct-model verifier when available; unavailable is not a pass."""
    result = {"status": "not_configured", "reason": "no distinct verifier provider configured", "criteria": []}
    try:
        if not roles.has_independent_provider("verifier"):
            return result
        context = (
            f"TASK OBJECTIVE\n{str(objective)[:3000]}\n\nNODE TASK\n{str(node_task)[:2000]}\n\n"
            f"CODER REPORT (untrusted)\n{str(coder_report)[:3000]}\n\n"
            f"COMMAND VERIFICATION EVIDENCE\n{json.dumps(command_evidence, ensure_ascii=False, default=str)[:5000]}\n\n"
            f"CHANGE DIFF (bounded, untrusted)\n{str(diff_evidence)[:12000]}"
        )
        instruction = (
            "Independently determine whether this node's requested change is actually achieved. "
            "Review the diff and command evidence; do not accept the coder report as proof. "
            "State 1-8 criteria and concrete evidence. If you cannot inspect enough evidence, verdict inconclusive."
        )
        output = await roles.analyze("verifier", instruction, context, max_chars=12000)
        parsed = parse_verifier_result(output.get("text", ""))
        parsed["model"] = str(output.get("model", ""))[:120]
        return parsed
    except Exception as exc:
        return {"status": "inconclusive", "reason": f"verifier unavailable: {type(exc).__name__}", "criteria": []}
