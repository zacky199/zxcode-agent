"""Evidence-grounded progress classification for DAG node executions."""
from __future__ import annotations

from typing import Any



def classify_node_progress(*, fatal: bool, journal_closed: bool,
                          acceptance_accepted: bool, diff: str = "",
                          verification: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify execution evidence without treating output as proof of code correctness."""
    verification = verification if isinstance(verification, dict) else {}
    verification_status = verification.get("status", "not_configured")
    independent = verification.get("independent_verification", {})
    independent_status = independent.get("status", "not_configured") if isinstance(independent, dict) else "not_configured"
    if not journal_closed:
        status = "uncertain"
        reason = "mutation journal did not close; workspace outcome may be incomplete"
    elif fatal or not acceptance_accepted or verification_status == "failed" or independent_status == "failed":
        status = "failed"
        reason = "execution acceptance or verification failed"
    elif verification_status == "passed" and independent_status == "verified":
        status = "independently_verified"
        reason = "independent verifier confirmed task criteria with execution evidence"
    elif isinstance(diff, str) and diff.strip():
        status = "workspace_changed"
        reason = "mutation journal reported workspace diff; change is not semantic correctness proof"
    elif verification_status == "passed":
        status = "verified_without_workspace_change"
        reason = "verification passed but mutation journal reported no workspace diff"
    else:
        status = "no_workspace_change"
        reason = "no workspace diff or configured verification evidence was reported"
    return {
        "status": status,
        "reason": reason,
        "journal_closed": bool(journal_closed),
        "acceptance_accepted": bool(acceptance_accepted),
        "verification_status": verification_status,
        "workspace_changed": bool(isinstance(diff, str) and diff.strip()),
        "independent_verification_status": independent_status,
    }
