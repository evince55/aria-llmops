import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals import routing_efficiency_eval as ev
from telemetry import schema


def _usage(task, imputed, session_id, model="claude-opus-4-8"):
    return schema.make_usage_event(
        harness="claude-code", session_id=session_id, msg_id=str(id(task)) + task[:3],
        model=model, imputed_usd=imputed, actual_usd=0.0, task_text=task,
    )


def test_efficiency_aggregates_by_session():
    events = [
        _usage("rename a variable", 0.5, "s1"),            # SIMPLE -> local-first tier
        _usage("design the auth flow security", 2.0, "s2"),  # CRITICAL
    ]
    res = ev.evaluate(events)
    assert res["n_sessions"] == 2
    assert res["n_usage_events"] == 2
    assert res["total_imputed_usd"] == 2.5
    assert res["local_first_sessions_pct"] == 50.0
    assert sum(res["by_complexity"].values()) == 2


def test_multiple_events_one_session_counted_once():
    # task_text is copied onto every event in a session; classification must
    # count it once, while cost still sums across all events.
    events = [
        _usage("refactor the module", 1.0, "s1"),
        _usage("refactor the module", 1.0, "s1"),
        _usage("refactor the module", 1.0, "s1"),
    ]
    res = ev.evaluate(events)
    assert res["n_sessions"] == 1              # one session, not three
    assert res["n_usage_events"] == 3
    assert res["total_imputed_usd"] == 3.0     # cost still sums
    assert sum(res["by_complexity"].values()) == 1  # classified once


def test_ignores_events_without_task_text():
    events = [{"event": "usage", "imputed_usd": 1.0, "task_text": None, "model": "claude-opus-4-8"}]
    res = ev.evaluate(events)
    assert res["n_sessions"] == 0
