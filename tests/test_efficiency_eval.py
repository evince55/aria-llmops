import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals import routing_efficiency_eval as ev
from telemetry import schema


def _usage(task, imputed, model="claude-opus-4-8"):
    return schema.make_usage_event(
        harness="claude-code", session_id="s", msg_id=str(id(task)) + task[:3],
        model=model, imputed_usd=imputed, actual_usd=0.0, task_text=task,
    )


def test_efficiency_aggregates(tmp_path):
    events = [
        _usage("rename a variable", 0.5),          # SIMPLE -> would route local
        _usage("design the auth flow security", 2.0),  # CRITICAL
    ]
    res = ev.evaluate(events)
    assert res["n_tasks"] == 2
    assert res["total_imputed_usd"] == 2.5
    assert 0.0 <= res["would_route_local_pct"] <= 100.0
    assert sum(res["by_complexity"].values()) == 2


def test_ignores_events_without_task_text():
    events = [{"event": "usage", "imputed_usd": 1.0, "task_text": None, "model": "claude-opus-4-8"}]
    res = ev.evaluate(events)
    assert res["n_tasks"] == 0
