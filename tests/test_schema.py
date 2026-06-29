import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from telemetry import schema


def test_make_usage_event_shape():
    e = schema.make_usage_event(
        harness="claude-code", session_id="s1", msg_id="m1",
        model="claude-opus-4-8", input_tokens=10, output_tokens=2,
        cost_model="subscription", actual_usd=0.0, imputed_usd=0.001,
    )
    assert e["event"] == "usage"
    assert e["harness"] == "claude-code"
    assert e["cost_model"] == "subscription"
    assert e["outcome"] is None


def test_dedup_key_for_usage_and_none_for_decision():
    u = schema.make_usage_event(harness="claude-code", session_id="s1", msg_id="m1", model="x")
    d = schema.make_route_decision_event(
        harness="opencode", task_text="t", complexity="SIMPLE",
        chosen_model="llama-cpp/qwen35b", estimated_usd=0.0, alternatives=[],
    )
    assert schema.dedup_key(u) == "claude-code|s1|m1"
    assert schema.dedup_key(d) is None


def test_append_is_idempotent(tmp_path):
    ledger = tmp_path / "events.jsonl"
    u = schema.make_usage_event(harness="claude-code", session_id="s1", msg_id="m1", model="x")
    assert schema.append_events([u], ledger=ledger) == 1
    assert schema.append_events([u], ledger=ledger) == 0  # duplicate skipped
    assert len(schema.read_events(ledger=ledger)) == 1


def test_route_decisions_always_append(tmp_path):
    ledger = tmp_path / "events.jsonl"
    d = schema.make_route_decision_event(
        harness="opencode", task_text="t", complexity="SIMPLE",
        chosen_model="llama-cpp/qwen35b", estimated_usd=0.0, alternatives=[],
    )
    assert schema.append_events([d], ledger=ledger) == 1
    assert schema.append_events([d], ledger=ledger) == 1  # no dedup key -> appended again
    assert len(schema.read_events(ledger=ledger)) == 2
