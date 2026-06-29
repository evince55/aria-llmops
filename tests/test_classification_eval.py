import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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
