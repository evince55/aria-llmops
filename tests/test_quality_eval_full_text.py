"""Regression: the quality eval must classify the FULL task text.

The downgrade-candidate annotation previously classified the same 80-char
string it displays, so any tier signal past char 80 was invisible: a frontier
success whose CRITICAL keyword ("...credentials in the keychain") sat beyond
the cut was confidently mislabeled a cheap-tier strong downgrade candidate —
the eval recommending we route SECURITY work to a cheap model.
"""
from evals.routing_quality_eval import evaluate
from llmops import ModelRouter


def _usage(task_text):
    return [{"event": "usage", "session_id": "s1", "msg_id": "m1",
             "model": "claude-opus-4-8", "imputed_usd": 9.0,
             "outcome": "success", "task_text": task_text}]


# Padding pushes the CRITICAL keyword past the 80-char display cut.
LONG_CRITICAL = ("x " * 45) + "add encryption for stored credentials in the keychain"


def test_classifier_sees_text_beyond_display_cut():
    seen = {}

    def spy(task):
        seen["task"] = task
        return ModelRouter(log_decisions=False).classify_detailed(task)

    evaluate(_usage(LONG_CRITICAL), classify=spy)
    assert seen["task"] == LONG_CRITICAL          # full text, not 80 chars
    assert len(seen["task"]) > 80


def test_late_critical_signal_blocks_strong_downgrade():
    router = ModelRouter(log_decisions=False)
    res = evaluate(_usage(LONG_CRITICAL), classify=router.classify_detailed)
    cand = res["downgrade_candidates"][0]
    assert cand["router_tier"] == "CRITICAL"       # was MODERATE pre-fix
    assert res["strong_downgrade_candidates"] == []  # security work is not "downgrade it"


def test_display_task_stays_truncated_and_no_internal_field_leaks():
    router = ModelRouter(log_decisions=False)
    res = evaluate(_usage(LONG_CRITICAL), classify=router.classify_detailed)
    cand = res["downgrade_candidates"][0]
    assert len(cand["task"]) <= 80
    assert "_full_task" not in cand


def test_no_classifier_output_schema_unchanged():
    res = evaluate(_usage(LONG_CRITICAL))          # classify=None path
    cand = res["downgrade_candidates"][0]
    assert "_full_task" not in cand
    assert "router_tier" not in cand
