from execution.progress import classify_node_progress


def classify(**overrides):
    args = dict(fatal=False, journal_closed=True, acceptance_accepted=True,
                diff="", verification={"status": "not_configured"})
    args.update(overrides)
    return classify_node_progress(**args)



def test_workspace_diff_is_change_evidence_not_correctness_claim():
    result = classify(diff="diff --git a/file b/file\n+change")
    assert result["status"] == "workspace_changed"
    assert "not semantic correctness proof" in result["reason"]
    assert result["workspace_changed"] is True



def test_verification_pass_without_diff_is_distinct_from_mutation():
    result = classify(verification={"status": "passed"})
    assert result["status"] == "verified_without_workspace_change"



def test_no_diff_without_verification_is_not_claimed_as_progress():
    result = classify()
    assert result["status"] == "no_workspace_change"



def test_fatal_execution_is_failed():
    result = classify(fatal=True)
    assert result["status"] == "failed"



def test_unclosed_journal_is_uncertain_even_if_execution_was_fatal():
    result = classify(fatal=True, journal_closed=False)
    assert result["status"] == "uncertain"


def test_independent_verifier_pass_marks_node_independently_verified():
    result = classify(diff="change", verification={"status": "passed",
        "independent_verification": {"status": "verified"}})
    assert result["status"] == "independently_verified"
    assert result["independent_verification_status"] == "verified"


def test_independent_verifier_failure_blocks_progress():
    result = classify(diff="change", verification={"status": "passed",
        "independent_verification": {"status": "failed"}})
    assert result["status"] == "failed"


def test_independent_verification_status_is_checkpoint_friendly():
    result = classify(diff="some change", verification={"status": "passed",
        "independent_verification": {"status": "inconclusive"}})
    assert result["independent_verification_status"] == "inconclusive"
    assert result["status"] == "workspace_changed"
