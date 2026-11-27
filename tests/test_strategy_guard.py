from planner.strategy_guard import failure_fingerprint, is_strategy_diverse, similarity


def test_failure_fingerprint_is_stable_across_whitespace_and_timestamp_variants():
    first = failure_fingerprint(tool_name="shell", outcome="failed", error_text="boom\n  at 2026-11-27 10:12:13")
    second = failure_fingerprint(tool_name="shell", outcome="failed", error_text=" boom at 2026/11/28 11:12:14 ")
    assert first == second
    assert len(first) == 24


def test_failure_fingerprint_changes_when_failure_evidence_changes():
    a = failure_fingerprint(tool_name="shell", outcome="failed", error_text="permission denied")
    b = failure_fingerprint(tool_name="shell", outcome="failed", error_text="file not found")
    assert a != b


def test_strategy_guard_rejects_near_verbatim_repetition():
    original = "Implement the authentication endpoint using the existing route handler and add a test."
    repeated = ["Implement the authentication endpoint using the existing route handler and add a test.",
                "Run the tests"]
    assert not is_strategy_diverse(original, repeated)
    assert similarity(original, repeated[0]) >= 0.88


def test_strategy_guard_accepts_materially_different_breakdown():
    original = "Implement the authentication endpoint using the existing route handler and add a test."
    alternative = ["Inspect authentication middleware and map the current token flow.",
                  "Create a focused unit test for invalid token handling.",
                  "Refactor endpoint integration after the isolated test passes."]
    assert is_strategy_diverse(original, alternative)
