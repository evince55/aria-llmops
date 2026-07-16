import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from telemetry.flywheel import export_pairs


def _decision(task, session=None, tier="SIMPLE", harness="claude-code"):
    return {"event": "route_decision", "ts": "2026-07-14T00:00:00+00:00",
            "harness": harness, "task_text": task, "complexity": tier,
            "chosen_model": "llama-cpp/qwen35b", "estimated_usd": 0.0,
            "alternatives": [], "session_id": session}


def _usage(session, task, outcome=None):
    return {"event": "usage", "session_id": session, "task_text": task,
            "model": "claude-opus-4-8", "imputed_usd": 1.0, "outcome": outcome,
            "msg_id": f"{session}-m"}


def test_export_joins_outcome_via_session_id():
    ev = [_decision("fix the login bug", session="s1"),
          _usage("s1", "fix the login bug", outcome="success")]
    pairs = export_pairs(ev)
    assert len(pairs) == 1
    p = pairs[0]
    assert p["task_text"] == "fix the login bug"
    assert p["tier"] == "SIMPLE"
    assert p["outcome"] == "success"


def test_export_falls_back_to_task_text_prefix_join():
    # Legacy decisions have no session_id; join on task-text prefix instead
    # (session task_text is the clipped first prompt).
    ev = [_decision("fix the login bug in AuthManager", session=None),
          _usage("s9", "fix the login bug in AuthManager", outcome="failure")]
    pairs = export_pairs(ev)
    assert pairs[0]["outcome"] == "failure"


def test_export_keeps_unjoined_decisions_with_null_outcome():
    ev = [_decision("standalone prompt with no session", session=None)]
    pairs = export_pairs(ev)
    assert len(pairs) == 1 and pairs[0]["outcome"] is None


def test_export_dedups_identical_task_tier_pairs():
    ev = [_decision("fix a typo", session="s1"), _decision("fix a typo", session="s1")]
    assert len(export_pairs(ev)) == 1


VAGUE = "hmm something feels wrong with playback lately somehow"


def test_export_enriches_defaulted_tiers_with_model_classifier():
    # 30/32 real pairs carry the keyword MODERATE *default* — useless as
    # training labels. With a classifier injected, defaulted pairs get the
    # model's tier (tier_source="model"); keyword-confident pairs keep their
    # keyword tier untouched (tier_source="keyword").
    ev = [_decision(VAGUE, session="s1", tier="MODERATE"),
          _decision("fix a typo in the readme file", session="s2", tier="SIMPLE")]
    pairs = export_pairs(ev, classify=lambda t: ("COMPLEX", "model"))
    by_task = {p["task_text"]: p for p in pairs}
    assert by_task[VAGUE]["tier"] == "COMPLEX"
    assert by_task[VAGUE]["tier_source"] == "model"
    assert by_task["fix a typo in the readme file"]["tier"] == "SIMPLE"
    assert by_task["fix a typo in the readme file"]["tier_source"] == "keyword"


def test_export_keeps_default_when_classifier_falls_back():
    # 9B unreachable -> classify_via_model returns keyword-fallback; the pair
    # keeps its stored default and is marked so training can exclude it.
    ev = [_decision(VAGUE, session="s1", tier="MODERATE")]
    pairs = export_pairs(ev, classify=lambda t: ("MODERATE", "keyword-fallback"))
    assert pairs[0]["tier"] == "MODERATE"
    assert pairs[0]["tier_source"] == "keyword-default"


def test_export_without_classifier_still_marks_tier_source():
    ev = [_decision(VAGUE, session="s1", tier="MODERATE"),
          _decision("fix a typo in the readme file", session="s2", tier="SIMPLE")]
    pairs = export_pairs(ev)
    by_task = {p["task_text"]: p for p in pairs}
    assert by_task[VAGUE]["tier_source"] == "keyword-default"
    assert by_task["fix a typo in the readme file"]["tier_source"] == "keyword"


def test_export_quarantines_eval_set_tasks():
    # Tasks that appear in the labeled eval datasets must NEVER become training
    # pairs — they are the held-out measurement instrument.
    ev = [_decision("fix a typo in the README", session="s1"),   # in labeled_tasks.jsonl
          _decision("a totally novel task about the queue", session="s2")]
    pairs = export_pairs(ev)
    texts = [p["task_text"] for p in pairs]
    assert "fix a typo in the README" not in texts
    assert "a totally novel task about the queue" in texts
    quarantined = [p for p in export_pairs(ev, include_quarantined=True)
                   if p.get("quarantined")]
    assert len(quarantined) == 1
