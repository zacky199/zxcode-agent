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
