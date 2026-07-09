"""Regression: the dashboard must not headline the tuning-set accuracy.

The classification block previously showed ONE number — accuracy on
labeled_tasks.jsonl, the very dataset the keywords were tuned against
(self-fulfilling: 100%). The keyword-blind prose set scores ~0.33 with the
keyword classifier; that is the honest out-of-distribution number and must be
the headline, with the tuning-set score explicitly labeled as such.
"""
from dashboard import generate as dash
from evals.router_classification_eval import load_dataset, evaluate
from pathlib import Path

_DS = Path(dash.__file__).resolve().parents[1] / "evals" / "datasets"


def _classification():
    return {
        "keyword_tuned": evaluate(load_dataset(_DS / "labeled_tasks.jsonl")),
        "prose_blind": evaluate(load_dataset(_DS / "labeled_tasks_prose.jsonl")),
    }


def test_prose_blind_number_is_present_and_labeled_honest():
    html = dash.build_html([], classification=_classification())
    assert "keyword-blind prose" in html
    assert "the honest number" in html


def test_tuned_number_is_labeled_self_fulfilling():
    html = dash.build_html([], classification=_classification())
    assert "self-fulfilling" in html
    assert "tuning target" in html


def test_generate_includes_both_datasets(tmp_path):
    from telemetry import schema
    ledger = tmp_path / "events.jsonl"
    schema.append_events([schema.make_usage_event(
        harness="claude-code", session_id="s", msg_id="m1",
        model="claude-opus-4-8", imputed_usd=1.0, task_text="t")], ledger=ledger)
    p = dash.generate(ledger=ledger, out=tmp_path / "index.html")
    html = p.read_text(encoding="utf-8")
    assert "keyword-blind prose" in html and "tuning target" in html


def test_prose_accuracy_really_is_far_below_tuned():
    # The honesty gap this PR surfaces, pinned numerically: keyword accuracy
    # on the tuned set is ~1.0, on keyword-blind prose it collapses.
    c = _classification()
    assert c["keyword_tuned"]["accuracy"] >= 0.9
    assert c["prose_blind"]["accuracy"] <= 0.5
