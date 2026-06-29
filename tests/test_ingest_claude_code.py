import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pathlib import Path
from telemetry import ingest_claude_code as ing
from telemetry import schema, pricing

FIX = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def test_parse_extracts_one_event_per_assistant_message():
    events = ing.parse_transcript(FIX)
    assert len(events) == 2  # two assistant lines; user/system skipped
    assert all(e["event"] == "usage" for e in events)


def test_parse_populates_tokens_model_and_task_text():
    e = ing.parse_transcript(FIX)[0]
    assert e["model"] == "claude-opus-4-8"
    assert e["input_tokens"] == 1000 and e["output_tokens"] == 200
    assert e["cache_write_tokens"] == 500 and e["cache_read_tokens"] == 4000
    assert e["harness"] == "claude-code"
    assert e["session_id"] == "sess-fixture"
    assert e["msg_id"] == "req-1"
    assert e["task_text"].startswith("Add a disk-full guard")
    assert e["cost_model"] == "subscription" and e["actual_usd"] == 0.0


def test_parse_computes_imputed_cost():
    e = ing.parse_transcript(FIX)[0]
    expected = pricing.imputed_usd(
        "claude-opus-4-8", input_tokens=1000, output_tokens=200,
        cache_write_tokens=500, cache_read_tokens=4000,
    )
    assert e["imputed_usd"] == expected and expected > 0


def test_ingest_is_idempotent(tmp_path):
    ledger = tmp_path / "events.jsonl"
    assert ing.ingest([FIX], ledger=ledger) == 2
    assert ing.ingest([FIX], ledger=ledger) == 0
    assert len(schema.read_events(ledger=ledger)) == 2
