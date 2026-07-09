"""Regression: negated approval must not score as SUCCESS.

Pre-guard, `\\bmerge\\b` (and friends) matched inside negated instructions, so
"don't merge this yet", "do not merge until I test it", and even the standing
rule "never merge to main directly" all labeled a session SUCCESS. Those labels
feed the routing-quality eval; a false success on a cheap-routed session hides
exactly the regression the eval exists to catch.
"""
from telemetry.outcomes import outcome_from_user_texts


# -- the bug: negated approvals ------------------------------------------------

def test_dont_merge_is_not_success():
    assert outcome_from_user_texts(["don't merge this yet, it breaks navigation"]) is None


def test_do_not_merge_is_not_success():
    assert outcome_from_user_texts(["do not merge until I test it"]) is None


def test_never_merge_rule_is_not_success():
    assert outcome_from_user_texts(["never merge to main directly"]) is None


def test_not_perfect_is_not_success():
    assert outcome_from_user_texts(["hmm, not perfect"]) is None


def test_negator_with_intervening_words():
    assert outcome_from_user_texts(["do not immediately merge it"]) is None


# -- approvals still work ------------------------------------------------------

def test_plain_merge_still_success():
    assert outcome_from_user_texts(["great, merge it"]) == "success"


def test_works_now_still_success():
    assert outcome_from_user_texts(["works now, thanks"]) == "success"


def test_lgtm_still_success():
    assert outcome_from_user_texts(["lgtm"]) == "success"


# -- failure side untouched (its phrases embed their own negation) --------------

def test_doesnt_work_still_failure():
    assert outcome_from_user_texts(["this doesn't work"]) == "failure"


def test_failure_then_negated_merge_stays_failure():
    assert outcome_from_user_texts(["still broken", "don't merge"]) == "failure"


def test_later_clean_approval_still_overrides_failure():
    assert outcome_from_user_texts(["still broken", "works now, merge it"]) == "success"
