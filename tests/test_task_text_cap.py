"""Regression: the ledger caps task_text at the SCHEMA layer for every event.

Pre-fix the cap was each caller's responsibility: usage callers truncated by
hand while make_route_decision_event stored the routed task verbatim — one
multi-page prompt wrote its full text (probe: 20,000 chars) into every
route_decision event, bloating an append-only file that is never rewritten in
normal operation.
"""
from telemetry import schema


LONG = "y" * 20_000


def test_route_decision_task_text_is_capped():
    e = schema.make_route_decision_event(
        harness="t", task_text=LONG, complexity="SIMPLE",
        chosen_model="m", estimated_usd=0.0, alternatives=[])
    assert len(e["task_text"]) == schema.TASK_TEXT_MAX


def test_usage_task_text_is_capped_even_if_caller_forgets():
    e = schema.make_usage_event(harness="t", session_id="s", msg_id="m",
                                model="m", task_text=LONG)
    assert len(e["task_text"]) == schema.TASK_TEXT_MAX


def test_short_and_none_task_text_pass_through():
    e = schema.make_usage_event(harness="t", session_id="s", msg_id="m",
                                model="m", task_text="short")
    assert e["task_text"] == "short"
    e2 = schema.make_route_decision_event(
        harness="t", task_text=None, complexity="SIMPLE",
        chosen_model="m", estimated_usd=0.0, alternatives=[])
    assert e2["task_text"] is None


def test_ingest_cap_is_the_schema_cap():
    from telemetry import ingest_claude_code as cc
    assert cc.TASK_TEXT_MAX == schema.TASK_TEXT_MAX == 500
