import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_comparison_includes_default_moderate_baseline():
    # NVIDIA-Table-1-style ablations need the no-router floor: a strategy that
    # always answers MODERATE. It must exist and be constant.
    from llmops import ModelRouter
    from evals.classifier_comparison import _strategies
    strats = _strategies(ModelRouter(log_decisions=False))
    assert "default-MODERATE" in strats
    assert strats["default-MODERATE"]("rotate the api keys") == "MODERATE"
from pathlib import Path
from evals import router_classification_eval as ev

DATA = Path(__file__).parent.parent / "evals" / "datasets" / "labeled_tasks.jsonl"


def test_dataset_loads():
    rows = ev.load_dataset(DATA)
    assert len(rows) >= 20
    assert all("task" in r and "expected_tier" in r for r in rows)


def test_evaluate_reports_accuracy_and_confusion():
    rows = ev.load_dataset(DATA)
    res = ev.evaluate(rows)
    assert res["n"] == len(rows)
    assert 0.0 <= res["accuracy"] <= 1.0
    assert set(res["per_tier"]).issubset({"CRITICAL", "COMPLEX", "MODERATE", "SIMPLE"})
    assert "confusion" in res
    # Sanity: the seed set is keyword-aligned, so the classifier should do well.
    assert res["accuracy"] >= 0.7


def test_evaluate_accepts_custom_classify_strategy():
    rows = [{"task": "a", "expected_tier": "SIMPLE"},
            {"task": "b", "expected_tier": "COMPLEX"}]
    res = ev.evaluate(rows, classify=lambda t: "SIMPLE")
    assert res["accuracy"] == 0.5   # SIMPLE row right, COMPLEX row wrong


def test_prose_dataset_keyword_blind_except_severity():
    """The prose set is the keyword-blind regime for the bulk distribution: the
    keyword classifier must default (matched=False) on every NON-CRITICAL row.
    CRITICAL rows MAY now match — the consequence-based severity patterns are
    meant to catch data-loss / exposure prose even when the 9B is unavailable."""
    from llmops import ModelRouter
    prose = ev.load_dataset(DATA.parent / "labeled_tasks_prose.jsonl")
    r = ModelRouter(log_decisions=False)
    assert len(prose) >= 15
    for row in prose:
        if row["expected_tier"] != "CRITICAL":
            assert r.classify_detailed(row["task"])[1] is False, row["task"][:70]
