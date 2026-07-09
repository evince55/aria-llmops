"""Regression: valid-JSON NON-OBJECT lines must not crash transcript parsing.

parse_transcript documents itself as "defensive: tolerates missing fields and
skips unparseable lines" — but a line that parses to a non-dict (a bare JSON
string, array, or number) sailed past the ValueError guard and crashed the
whole transcript with AttributeError ('str' object has no attribute 'get').
Worse, the SessionEnd hook swallows exceptions by design, so the failing
session SILENTLY never reached the ledger: one stray line = invisible data
loss. Same crash existed in outcome_from_transcript and the backfill-outcomes
transcript loop.
"""
import json

from telemetry import ingest_claude_code as cc
from telemetry.outcomes import outcome_from_transcript


def _poisoned_transcript(tmp_path):
    p = tmp_path / "session.jsonl"
    rows = [
        '"a bare JSON string line"',
        "[1, 2, 3]",
        "42",
        json.dumps({"type": "user", "sessionId": "s-poison",
                    "message": {"content": "please fix the thing"}}),
        json.dumps({"type": "assistant", "sessionId": "s-poison", "uuid": "u1",
                    "message": {"model": "claude-opus-4-8",
                                "usage": {"input_tokens": 10, "output_tokens": 20}}}),
        json.dumps({"type": "user", "message": {"content": "works now, thanks"}}),
    ]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_parse_transcript_survives_non_object_lines(tmp_path):
    events = cc.parse_transcript(_poisoned_transcript(tmp_path))
    assert len(events) == 1                      # the real assistant event
    e = events[0]
    assert e["session_id"] == "s-poison"
    assert e["input_tokens"] == 10 and e["output_tokens"] == 20
    assert e["outcome"] == "success"             # outcome inference also survived


def test_outcome_from_transcript_survives_non_dict_entries():
    lines = ["bare-string-entry", [1, 2],
             {"type": "user", "message": {"content": "works now"}}]
    assert outcome_from_transcript(lines) == "success"


def test_ingest_end_to_end_with_poisoned_transcript(tmp_path):
    ledger = tmp_path / "events.jsonl"
    n = cc.ingest([_poisoned_transcript(tmp_path)], ledger=ledger)
    assert n == 1
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_backfill_outcomes_survives_poisoned_transcript(tmp_path, capsys):
    import telemetry as tcli
    from telemetry import schema
    proj = tmp_path / "proj"
    proj.mkdir()
    _poisoned_transcript(proj)
    ledger = tmp_path / "events.jsonl"
    schema.append_events([schema.make_usage_event(
        harness="claude-code", session_id="s-poison", msg_id="m1",
        model="claude-opus-4-8")], ledger=ledger)
    rc = tcli.main(["backfill-outcomes", "--ledger", str(ledger),
                    "--project-dir", str(proj), "--write"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sessions_with_outcome"] == 1
    assert out["events_updated"] == 1
    stamped = schema.read_events(ledger=ledger)[0]
    assert stamped["outcome"] == "success"
