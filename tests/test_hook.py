import sys, os, json, importlib.util
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pathlib import Path
from telemetry import schema

HOOK = Path(__file__).parent.parent / "telemetry" / "hooks" / "claude_code_session_end.py"


def _load():
    spec = importlib.util.spec_from_file_location("hook_mod", HOOK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_hook_ingests_transcript(tmp_path, monkeypatch):
    fix = os.path.join(os.path.dirname(__file__), "fixtures", "sample_transcript.jsonl")
    ledger = tmp_path / "events.jsonl"
    monkeypatch.setenv("LLMOPS_LEDGER", str(ledger))
    m = _load()
    rc = m.run(json.dumps({"transcript_path": fix}))
    assert rc == 0
    assert len(schema.read_events(ledger=ledger)) == 2


def test_hook_survives_bad_input(monkeypatch, tmp_path):
    monkeypatch.setenv("LLMOPS_LEDGER", str(tmp_path / "e.jsonl"))
    m = _load()
    assert m.run("not json") == 0          # never raises
    assert m.run(json.dumps({})) == 0       # missing transcript_path
