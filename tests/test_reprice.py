import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from telemetry import schema
from telemetry.reprice import reprice, reprice_event


def _opus_event(imputed):
    # 1M input + 1M output on opus-4-8 = $5 + $25 = $30 at current rates.
    return schema.make_usage_event(
        harness="claude-code", session_id="s", msg_id="m", model="claude-opus-4-8",
        input_tokens=1_000_000, output_tokens=1_000_000, imputed_usd=imputed,
    )


def test_reprice_event_recomputes_imputed():
    updated, delta = reprice_event(_opus_event(90.0))  # old inflated value
    assert updated["imputed_usd"] == 30.0
    assert delta == -60.0


def test_reprice_passthrough_non_usage():
    e = schema.make_route_decision_event(
        harness="opencode", task_text="t", complexity="SIMPLE",
        chosen_model="llama-cpp/qwen35b", estimated_usd=0.0, alternatives=[],
    )
    updated, delta = reprice_event(e)
    assert updated == e and delta == 0.0


def test_reprice_dry_run_does_not_write(tmp_path):
    ledger = tmp_path / "events.jsonl"
    schema.append_events([_opus_event(90.0)], ledger=ledger)
    summary = reprice(ledger=ledger, write=False)
    assert summary["old_total_imputed_usd"] == 90.0
    assert summary["new_total_imputed_usd"] == 30.0
    assert summary["usage_repriced"] == 1
    assert summary["written"] is False
    assert schema.read_events(ledger)[0]["imputed_usd"] == 90.0  # untouched


def test_reprice_write_rewrites_ledger(tmp_path):
    ledger = tmp_path / "events.jsonl"
    schema.append_events([_opus_event(90.0)], ledger=ledger)
    summary = reprice(ledger=ledger, write=True)
    assert summary["written"] is True
    assert schema.read_events(ledger)[0]["imputed_usd"] == 30.0
