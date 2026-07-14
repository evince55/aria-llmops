import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmops import ModelRouter, CodingMemory, CostMonitor
from telemetry import schema


def _router(tmp_path):
    mem = CodingMemory(tmp_path / "mem.json")
    mon = CostMonitor(mem)
    return ModelRouter(mem, mon, ledger=tmp_path / "events.jsonl")


def test_route_task_logs_a_decision(tmp_path):
    r = _router(tmp_path)
    r.route_task("refactor the audio engine for performance", estimated_tokens=2000)
    events = schema.read_events(ledger=tmp_path / "events.jsonl")
    decisions = [e for e in events if e["event"] == "route_decision"]
    assert len(decisions) == 1
    d = decisions[0]
    assert d["harness"] == "opencode"
    assert d["complexity"] in ("CRITICAL", "COMPLEX", "MODERATE", "SIMPLE")
    assert "chosen_model" in d and "alternatives" in d


def test_route_task_logs_session_id_when_given(tmp_path):
    # The flywheel harvester joins route_decisions to per-session outcomes, so
    # the hook must be able to stamp the session id onto the decision event.
    r = _router(tmp_path)
    r.route_task("fix a typo in the README", session_id="sess-42")
    d = [e for e in schema.read_events(ledger=tmp_path / "events.jsonl")
         if e["event"] == "route_decision"][0]
    assert d["session_id"] == "sess-42"


def test_route_task_session_id_defaults_to_none(tmp_path):
    r = _router(tmp_path)
    r.route_task("fix a typo in the README")
    d = [e for e in schema.read_events(ledger=tmp_path / "events.jsonl")
         if e["event"] == "route_decision"][0]
    assert d["session_id"] is None


def test_logging_can_be_disabled(tmp_path):
    mem = CodingMemory(tmp_path / "mem.json")
    r = ModelRouter(mem, CostMonitor(mem), log_decisions=False, ledger=tmp_path / "events.jsonl")
    r.route_task("rename a variable")
    assert schema.read_events(ledger=tmp_path / "events.jsonl") == []
