from execution.acceptance import NodeAcceptanceContract


def test_node_acceptance_requires_clean_loop_nonempty_result_and_closed_journal():
    result = NodeAcceptanceContract.evaluate(
        result="Implemented and checked.", fatal=False, journal_closed=True)
    assert result.accepted is True
    assert all(result.checks.values())


def test_node_acceptance_rejects_empty_result():
    result = NodeAcceptanceContract.evaluate(result="  ", fatal=False, journal_closed=True)
    assert result.accepted is False
    assert result.checks["final_response_present"] is False


def test_node_acceptance_rejects_fatal_or_unclosed_journal():
    result = NodeAcceptanceContract.evaluate(
        result="Some output", fatal=True, journal_closed=False)
    assert result.accepted is False
    assert result.checks["react_loop_terminated_cleanly"] is False
    assert result.checks["mutation_journal_closed"] is False


def test_acceptance_result_is_json_safe():
    result = NodeAcceptanceContract.evaluate(
        result="ok", fatal=False, journal_closed=True).to_dict()
    assert result == {
        "accepted": True,
        "checks": {
            "react_loop_terminated_cleanly": True,
            "final_response_present": True,
            "mutation_journal_closed": True,
            "node_verification_passed": True,
        },
        "evidence": "Execution contract satisfied.",
    }
