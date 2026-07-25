"""Tests for the input/output token split (A1).

Why this exists: output tokens cost 3-4x input on every cloud model priced here
(minimax-m3 is $0.30 in / $1.20 out), so any claim about brevity saving money is
unfalsifiable without the split. A2 (terse-output A/B) and A3 (brevity cap in the
distill filter) both depend on it — without A1 they are vibes.

THE TRAP THIS MODULE EXISTS TO AVOID: with prompt caching, "output ratio" is
meaningless until you say what the denominator is. In this repo's own telemetry
the cache-read total is 2.3 BILLION tokens against 874K input and 6.5M output, so
out/(in+out) reads 0.882 while out/(in+out+cache) reads 0.003 — a 300x spread
over the same events. Both are reported, always labelled, and never averaged.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry.token_split import (  # noqa: E402
    DEFAULT_OUTPUT_RATIO, by_model, measured_output_ratio, per_session_output,
    split_totals,
)


def ev(model="m", i=0, o=0, cw=0, cr=0, session="s1", event="usage"):
    return {"event": event, "model": model, "session_id": session,
            "input_tokens": i, "output_tokens": o,
            "cache_write_tokens": cw, "cache_read_tokens": cr}


class TestSplitTotals:
    def test_sums_each_token_class(self):
        t = split_totals([ev(i=100, o=50, cw=10, cr=1000), ev(i=200, o=150)])
        assert t["input_tokens"] == 300
        assert t["output_tokens"] == 200
        assert t["cache_write_tokens"] == 10
        assert t["cache_read_tokens"] == 1000

    def test_reports_both_ratio_definitions(self):
        """One number would be a lie under caching — see the module docstring."""
        t = split_totals([ev(i=100, o=100, cr=9800)])
        assert t["output_ratio_excl_cache"] == pytest.approx(0.5)
        assert t["output_ratio_incl_cache"] == pytest.approx(0.01)

    def test_ignores_non_usage_events(self):
        t = split_totals([ev(i=100, o=50), ev(i=999, o=999, event="route_decision")])
        assert t["input_tokens"] == 100 and t["n_events"] == 1

    def test_empty_input_is_zero_not_a_division_error(self):
        t = split_totals([])
        assert t["output_ratio_excl_cache"] == 0.0
        assert t["output_ratio_incl_cache"] == 0.0
        assert t["n_events"] == 0

    def test_missing_and_null_fields_count_as_zero(self):
        t = split_totals([{"event": "usage", "model": "m", "output_tokens": None}])
        assert t["input_tokens"] == 0 and t["output_tokens"] == 0


class TestByModel:
    def test_groups_and_sorts_by_volume(self):
        rows = by_model([ev(model="small", i=1, o=1), ev(model="big", i=500, o=500)])
        assert [r["model"] for r in rows] == ["big", "small"]

    def test_carries_both_ratios_per_model(self):
        rows = by_model([ev(model="a", i=100, o=100, cr=9800)])
        assert rows[0]["output_ratio_excl_cache"] == pytest.approx(0.5)
        assert rows[0]["output_ratio_incl_cache"] == pytest.approx(0.01)

    def test_counts_events_per_model(self):
        rows = by_model([ev(model="a", o=1), ev(model="a", o=1), ev(model="b", o=1)])
        assert {r["model"]: r["n_events"] for r in rows} == {"a": 2, "b": 1}


class TestPerSessionOutput:
    """'Output tokens per task' — session is the finest grouping the events
    actually carry (there is no task_id), so it is named for what it is."""

    def test_sums_output_per_session(self):
        s = per_session_output([ev(session="a", o=10), ev(session="a", o=5), ev(session="b", o=100)])
        assert s["by_session"]["a"] == 15
        assert s["by_session"]["b"] == 100

    def test_reports_median_not_only_mean(self):
        """One runaway session should not set the headline number."""
        s = per_session_output([ev(session=str(i), o=10) for i in range(9)] + [ev(session="x", o=10_000)])
        assert s["median"] == 10
        assert s["mean"] > s["median"]

    def test_empty_is_safe(self):
        s = per_session_output([])
        assert s["median"] == 0 and s["mean"] == 0 and s["n_sessions"] == 0


class TestMeasuredOutputRatio:
    """The point of A1: replace an assumed constant with a measured one — but
    only when there is enough data to justify it."""

    def test_uses_the_measured_ratio_when_there_is_enough_data(self):
        rows = [ev(model="m", i=100, o=300) for _ in range(20)]
        assert measured_output_ratio(rows, "m", min_events=10) == pytest.approx(0.75)

    def test_falls_back_to_the_default_below_the_sample_floor(self):
        """Two calls must not be allowed to re-price the router."""
        rows = [ev(model="m", i=100, o=300) for _ in range(2)]
        assert measured_output_ratio(rows, "m", min_events=10) == DEFAULT_OUTPUT_RATIO

    def test_falls_back_for_a_model_with_no_events(self):
        assert measured_output_ratio([ev(model="other", o=1)], "m") == DEFAULT_OUTPUT_RATIO

    def test_default_is_overridable(self):
        assert measured_output_ratio([], "m", default=0.9) == 0.9

    def test_uses_the_cache_EXCLUSIVE_ratio(self):
        """Cost-wise, cached input is billed differently and is not part of the
        in-vs-out generation tradeoff a brevity experiment moves. Mixing cache in
        would make the ratio track prompt reuse, not verbosity."""
        rows = [ev(model="m", i=100, o=100, cr=100_000) for _ in range(20)]
        assert measured_output_ratio(rows, "m", min_events=10) == pytest.approx(0.5)

    def test_a_model_with_zero_tokens_falls_back(self):
        rows = [ev(model="m", i=0, o=0) for _ in range(20)]
        assert measured_output_ratio(rows, "m", min_events=10) == DEFAULT_OUTPUT_RATIO


class TestTheAssumedConstantIsPinned:
    """`CostMonitor.estimate_cost` prices every route off an ASSUMED output
    ratio. A1's job is to make that assumption visible and checkable, not to
    silently replace it — the telemetry on hand cannot validate a replacement
    for the router's own models (see the module docstring), so the value stays
    put and the two definitions are kept from drifting apart."""

    def test_llmops_and_telemetry_agree_on_the_default(self):
        import llmops
        assert llmops.DEFAULT_OUTPUT_RATIO == DEFAULT_OUTPUT_RATIO

    def test_estimate_cost_uses_the_named_constant_by_default(self):
        import llmops
        m = llmops.CostMonitor(llmops.CodingMemory())
        model = "opencode-go/minimax-m3"
        assert m.estimate_cost(model, 1_000_000) == pytest.approx(
            m.estimate_cost(model, 1_000_000, output_ratio=DEFAULT_OUTPUT_RATIO))

    def test_an_explicit_measured_ratio_changes_the_price(self):
        """The seam A2 needs: pass a measured ratio and the cost moves."""
        import llmops
        m = llmops.CostMonitor(llmops.CodingMemory())
        model = "opencode-go/minimax-m3"
        assert m.estimate_cost(model, 1_000_000, output_ratio=0.9) > \
            m.estimate_cost(model, 1_000_000, output_ratio=0.4)
