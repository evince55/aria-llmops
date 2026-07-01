import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import telemetry as cli
from telemetry import schema, ingest_claude_code as cc
from telemetry.outcomes import outcome_from_user_texts, outcome_from_transcript


def test_success_and_failure_and_none():
    assert outcome_from_user_texts(["this works, merge it"]) == "success"
    assert outcome_from_user_texts(["it still doesn't work"]) == "failure"
    assert outcome_from_user_texts(["please add a feature"]) is None


def test_last_decisive_signal_wins():
    assert outcome_from_user_texts(["it doesn't work", "great, works now"]) == "success"
    assert outcome_from_user_texts(["works", "actually it's still broken"]) == "failure"


def test_outcome_from_transcript_reads_user_turns():
    lines = [
        {"type": "user", "message": {"content": "add the thing"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
        {"type": "user", "message": {"content": "perfect, merge it"}},
    ]
    assert outcome_from_transcript(lines) == "success"


def _transcript(dir_, user_first, user_last, session_id="s1"):
    p = dir_ / f"{session_id}.jsonl"
    rows = [
        {"type": "user", "message": {"role": "user", "content": user_first}, "sessionId": session_id},
        {"type": "assistant", "uuid": "u1", "requestId": "r1", "sessionId": session_id,
         "timestamp": "2026-07-01T00:00:00Z", "cwd": "/x", "gitBranch": "main",
         "message": {"model": "claude-opus-4-8", "role": "assistant",
                     "content": [{"type": "text", "text": "ok"}],
                     "usage": {"input_tokens": 100, "output_tokens": 10}}},
        {"type": "user", "message": {"role": "user", "content": user_last}, "sessionId": session_id},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def test_ingest_stamps_outcome(tmp_path):
    p = _transcript(tmp_path, "build it", "works now, ship it")
    events = cc.parse_transcript(p)
    assert events and all(e["outcome"] == "success" for e in events)


def test_backfill_outcomes_updates_ledger(tmp_path):
    ledger = tmp_path / "events.jsonl"
    schema.append_events([schema.make_usage_event(
        harness="claude-code", session_id="s1", msg_id="m1", model="claude-opus-4-8",
        input_tokens=100, output_tokens=10, outcome=None)], ledger=ledger)
    proj = tmp_path / "proj"; proj.mkdir()
    _transcript(proj, "build it", "perfect, merge", session_id="s1")
    rc = cli.main(["backfill-outcomes", "--ledger", str(ledger), "--project-dir", str(proj), "--write"])
    assert rc == 0
    assert schema.read_events(ledger=ledger)[0]["outcome"] == "success"
