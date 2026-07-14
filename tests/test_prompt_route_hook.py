import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from telemetry import schema
from telemetry.hooks.claude_code_prompt_route import run


def _payload(prompt, session="sess-1"):
    return json.dumps({"prompt": prompt, "session_id": session, "cwd": "/x"})


def test_hook_logs_decision_with_session_id(tmp_path, monkeypatch):
    led = tmp_path / "events.jsonl"
    monkeypatch.setenv("LLMOPS_LEDGER", str(led))
    assert run(_payload("fix the race condition in the queue manager")) == 0
    events = schema.read_events(ledger=led)
    d = [e for e in events if e["event"] == "route_decision"]
    assert len(d) == 1
    assert d[0]["session_id"] == "sess-1"
    assert d[0]["harness"] == "claude-code"


def test_hook_filters_non_tasks(tmp_path, monkeypatch):
    led = tmp_path / "events.jsonl"
    monkeypatch.setenv("LLMOPS_LEDGER", str(led))
    for junk in ("/model opus", "yes", "# remember this", "!ls"):
        assert run(json.dumps({"prompt": junk, "session_id": "s"})) == 0
    assert schema.read_events(ledger=led) == []


def test_hook_never_raises_on_garbage_stdin():
    assert run("not json at all") == 0
