import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals.routing_quality_eval import evaluate, tier_of


def _u(session, model, usd, outcome=None, task="t"):
    return {"event": "usage", "session_id": session, "model": model,
            "imputed_usd": usd, "outcome": outcome, "task_text": task,
            "msg_id": f"{session}-{usd}-{model}"}


def test_tier_mapping():
    assert tier_of("claude-opus-4-8") == "frontier"
    assert tier_of("claude-sonnet-4-6") == "mid"
    assert tier_of("llama-cpp/qwen35b") == "local"
    assert tier_of("something-else") == "other"


def test_ignores_non_usage_events():
    ev = [{"event": "route_decision", "chosen_model": "x"}, _u("s1", "claude-opus-4-8", 3.0, "success")]
    r = evaluate(ev)
    assert r["n_sessions"] == 1 and r["n_labeled"] == 1


def test_cheap_routing_failure_flagged():
    # a FAILURE session that used a local model -> Q1 hit
    ev = [_u("s1", "claude-opus-4-8", 1.0, "failure"),
          _u("s1", "llama-cpp/qwen35b", 0.0, "failure")]
    r = evaluate(ev)
    assert len(r["cheap_routing_failures"]) == 1
    assert r["cheap_routing_failures"][0]["non_frontier_tiers"] == {"local": 1}


def test_frontier_only_failure_is_not_cheap_routing():
    # a failure entirely on the frontier model is NOT attributable to cheap routing
    ev = [_u("s1", "claude-opus-4-8", 2.0, "failure")]
    r = evaluate(ev)
    assert r["cheap_routing_failures"] == []


def test_downgrade_candidates_ranked_and_thresholded():
    ev = [
        _u("big", "claude-opus-4-8", 40.0, "success"),
        _u("mid", "claude-opus-4-8", 10.0, "success"),
        _u("tiny", "claude-opus-4-8", 1.0, "success"),      # below default $5 floor
        _u("mixed", "claude-opus-4-8", 30.0, "success"),
        _u("mixed", "claude-sonnet-4-6", 5.0, "success"),   # not frontier-only -> excluded
    ]
    r = evaluate(ev, min_candidate_usd=5.0)
    ids = [c["session_id"] for c in r["downgrade_candidates"]]
    assert ids == ["big", "mid"]            # sorted desc, tiny below floor, mixed excluded
    # addressable pool counts ALL frontier-only successes incl. sub-floor tiny
    assert r["addressable_frontier_success_usd"] == 51.0


def test_unlabeled_never_assumed():
    ev = [_u("s1", "claude-opus-4-8", 9.0, None)]
    r = evaluate(ev)
    assert r["outcomes"].get("unlabeled") == 1
    assert r["n_labeled"] == 0
    assert r["downgrade_candidates"] == []   # unlabeled is not a success


def test_usd_per_successful_session():
    ev = [_u("s1", "claude-opus-4-8", 10.0, "success"),
          _u("s2", "claude-opus-4-8", 30.0, "success")]
    r = evaluate(ev)
    assert r["usd_per_successful_session"] == 20.0


def test_no_classifier_omits_router_fields():
    ev = [_u("s1", "claude-opus-4-8", 10.0, "success", task="fix typo")]
    r = evaluate(ev)
    assert "router_classified" not in r
    assert "strong_downgrade_candidates" not in r
    assert "router_tier" not in r["downgrade_candidates"][0]


def test_classifier_annotates_and_filters_strong():
    # a cheap-tier success and a complex-tier success, both frontier-only
    ev = [_u("cheap", "claude-opus-4-8", 20.0, "success", task="fix a typo"),
          _u("hard", "claude-opus-4-8", 30.0, "success", task="rearchitect the engine")]
    def classify(task):
        return "SIMPLE" if "typo" in task else "COMPLEX"
    r = evaluate(ev, classify=classify)
    assert r["router_classified"] is True
    # both are downgrade candidates (frontier-only successes over floor), annotated
    tiers = {c["session_id"]: c["router_tier"] for c in r["downgrade_candidates"]}
    assert tiers == {"cheap": "SIMPLE", "hard": "COMPLEX"}
    # only the cheap-tier one is STRONG (router agrees a cheap model suffices)
    strong_ids = [c["session_id"] for c in r["strong_downgrade_candidates"]]
    assert strong_ids == ["cheap"]
    assert r["addressable_strong_usd"] == 20.0
    # addressable (all frontier successes) still counts both
    assert r["addressable_frontier_success_usd"] == 50.0


def test_strong_addressable_counts_sub_floor_sessions():
    # a $1 cheap-tier success is below the candidate floor but still counts toward
    # the strong-addressable pool
    ev = [_u("tiny", "claude-opus-4-8", 1.0, "success", task="rename a variable")]
    r = evaluate(ev, min_candidate_usd=5.0, classify=lambda t: "SIMPLE")
    assert r["downgrade_candidates"] == []           # below floor
    assert r["addressable_strong_usd"] == 1.0        # still addressable


def test_defaulted_moderate_is_not_strong():
    # classify_detailed-style: (tier, confident). A defaulted MODERATE
    # (confident=False) must NOT count as a strong downgrade candidate, even
    # though MODERATE is a "cheap" tier — the classifier just shrugged.
    ev = [_u("shrug", "claude-opus-4-8", 20.0, "success", task="ambiguous long task"),
          _u("sure", "claude-opus-4-8", 15.0, "success", task="fix a typo")]
    def classify(task):
        return ("MODERATE", False) if "ambiguous" in task else ("SIMPLE", True)
    r = evaluate(ev, classify=classify)
    strong_ids = [c["session_id"] for c in r["strong_downgrade_candidates"]]
    assert strong_ids == ["sure"]                    # defaulted MODERATE excluded
    assert r["addressable_strong_usd"] == 15.0
    # but both still appear as (weaker) downgrade candidates, annotated w/ confidence
    conf = {c["session_id"]: c["router_confident"] for c in r["downgrade_candidates"]}
    assert conf == {"shrug": False, "sure": True}
