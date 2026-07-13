"""run_task(log_usage=False) must skip the internal usage event so callers that
write their own outcome-labelled event (dashboard runner, judged harness) don't
double-count the execution in the ledger. Offline: stub executor, tmp ledger."""
from pathlib import Path

import llmops
from telemetry import schema


def _router(tmp_path):
    ledger = tmp_path / "events.jsonl"
    return llmops.ModelRouter(log_decisions=False, ledger=ledger), ledger


def _stub(prompt):
    return "stub output", {"prompt_tokens": 5, "completion_tokens": 7}


def test_default_logs_one_usage_event(tmp_path):
    r, ledger = _router(tmp_path)
    out = r.run_task("rename a local variable for clarity", executor=_stub)
    assert out["executed"] is True
    usage = [e for e in schema.read_events(ledger) if e.get("event") == "usage"]
    assert len(usage) == 1
    assert usage[0]["input_tokens"] == 5 and usage[0]["output_tokens"] == 7


def test_log_usage_false_writes_nothing(tmp_path):
    r, ledger = _router(tmp_path)
    out = r.run_task("rename a local variable for clarity", executor=_stub,
                     log_usage=False)
    assert out["executed"] is True                      # execution still happens
    assert out["usage"] == {"input_tokens": 5, "output_tokens": 7}  # caller gets the numbers
    events = schema.read_events(ledger) if Path(ledger).exists() else []
    assert [e for e in events if e.get("event") == "usage"] == []
