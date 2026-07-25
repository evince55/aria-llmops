"""Tests for the promotion rule itself.

`decide()` is the instrument that says ship-or-don't. It was written before the
first gate run so the threshold could not be tuned to the result, and it had no
tests — these pin the behaviour so a later edit cannot quietly loosen it.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.promotion_gate import (  # noqa: E402
    TIER_TOLERANCE, decide, load_baseline, load_rows, per_tier_recall,
)


def result(accuracy, **tiers):
    return {"accuracy": accuracy, "per_tier": {t: {"recall": r} for t, r in tiers.items()}}


BASE = dict(CRITICAL=0.93, COMPLEX=0.76, MODERATE=0.42, SIMPLE=0.88)


class TestDecide:
    def test_promotes_on_higher_accuracy_and_no_regression(self):
        inc = result(0.70, **BASE)
        chal = result(0.75, **{**BASE, "MODERATE": 0.75})
        v = decide(inc, chal)
        assert v["promote"] is True
        assert v["tier_regressions"] == {}
        assert v["accuracy_delta"] == pytest.approx(0.05)

    def test_equal_accuracy_still_promotes(self):
        """The rule is >=, not >: an equal-accuracy challenger that regresses
        nothing is a legitimate win when it is half the size."""
        inc = result(0.70, **BASE)
        assert decide(inc, result(0.70, **BASE))["promote"] is True

    def test_rejects_on_tier_regression_despite_better_accuracy(self):
        """The real 2026-07-20 outcome: +4 points of accuracy, -18 on COMPLEX."""
        inc = result(0.705, **BASE)
        chal = result(0.744, **{**BASE, "COMPLEX": 0.579, "MODERATE": 0.75})
        v = decide(inc, chal)
        assert v["promote"] is False
        assert v["accuracy_ok"] is True
        assert "COMPLEX" in v["tier_regressions"]
        assert v["tier_regressions"]["COMPLEX"] == pytest.approx(-0.181)

    def test_rejects_on_lower_accuracy_even_with_no_regression(self):
        inc = result(0.75, **BASE)
        chal = result(0.70, **{k: v + 0.01 for k, v in BASE.items()})
        v = decide(inc, chal)
        assert v["promote"] is False
        assert v["accuracy_ok"] is False

    def test_regression_exactly_at_tolerance_is_allowed(self):
        """Boundary: the tolerance exists to absorb ~3-point noise on ~30 rows,
        so a drop OF exactly the tolerance must not reject."""
        inc = result(0.70, **BASE)
        chal = result(0.70, **{**BASE, "SIMPLE": BASE["SIMPLE"] - TIER_TOLERANCE})
        assert decide(inc, chal)["promote"] is True

    def test_regression_just_past_tolerance_rejects(self):
        inc = result(0.70, **BASE)
        chal = result(0.70, **{**BASE, "SIMPLE": BASE["SIMPLE"] - TIER_TOLERANCE - 0.001})
        assert decide(inc, chal)["promote"] is False

    def test_reports_every_regressing_tier_not_just_the_first(self):
        inc = result(0.70, **BASE)
        chal = result(0.70, **{**BASE, "COMPLEX": 0.30, "SIMPLE": 0.40})
        assert set(decide(inc, chal)["tier_regressions"]) == {"COMPLEX", "SIMPLE"}

    def test_missing_tier_counts_as_total_regression(self):
        """A challenger that never predicts a tier must not pass by omission."""
        inc = result(0.70, **BASE)
        chal = {"accuracy": 0.99, "per_tier": {"SIMPLE": {"recall": 1.0}}}
        v = decide(inc, chal)
        assert v["promote"] is False
        assert "CRITICAL" in v["tier_regressions"]

    def test_tolerance_is_overridable_but_defaults_to_the_declared_value(self):
        inc = result(0.70, **BASE)
        chal = result(0.70, **{**BASE, "COMPLEX": 0.60})
        assert decide(inc, chal)["promote"] is False
        assert decide(inc, chal, tolerance=0.20)["promote"] is True
        assert decide(inc, chal)["tolerance"] == TIER_TOLERANCE


class TestPerTierRecall:
    def test_extracts_and_rounds(self):
        assert per_tier_recall(result(0.5, CRITICAL=0.9312345)) == {"CRITICAL": 0.9312}

    def test_missing_per_tier_is_empty_not_an_error(self):
        assert per_tier_recall({"accuracy": 0.5}) == {}


class TestLoadRows:
    def test_skips_rows_without_a_label(self, tmp_path):
        p = tmp_path / "d.jsonl"
        p.write_text(
            json.dumps({"task": "a", "expected_tier": "SIMPLE"}) + "\n"
            + json.dumps({"task": "b", "expected_tier": None}) + "\n"
            + "\n"
            + json.dumps({"task": "", "expected_tier": "COMPLEX"}) + "\n"
        )
        rows = load_rows(p)
        assert len(rows) == 1 and rows[0]["task"] == "a"


# ---------------------------------------------------------------------------
# Cumulative-baseline checking (tolerance drift, found 2026-07-25)
# ---------------------------------------------------------------------------
class TestCumulativeBaseline:
    """A chain of individually-tolerated regressions must not drift unbounded.

    Measured: the S7 model swap cost SIMPLE -0.041 and the contested guard a
    further -0.021. Each passed the tolerance against its immediate incumbent;
    together they are -0.062, which does not. The rule as originally written
    only ever compared to the immediate predecessor, so nothing noticed.
    """

    # the real 176-row numbers from the drift that motivated this
    ORIGINAL = result(0.705, CRITICAL=0.931, COMPLEX=0.763, MODERATE=0.417, SIMPLE=0.878)
    S7 = result(0.761, CRITICAL=0.931, COMPLEX=0.789, MODERATE=0.600, SIMPLE=0.837)
    GUARD = result(0.784, CRITICAL=0.931, COMPLEX=0.789, MODERATE=0.683, SIMPLE=0.816)

    def test_without_a_baseline_the_rule_is_unchanged(self):
        """Backward compatibility: every existing caller keeps its semantics."""
        v = decide(self.S7, self.GUARD)
        assert v["promote"] is True
        assert v["tier_regressions"] == {}

    def test_the_measured_drift_is_now_caught(self):
        """The whole point: passes vs its incumbent, fails vs the original."""
        v = decide(self.S7, self.GUARD, baseline=self.ORIGINAL)
        assert v["promote"] is False
        assert v["tier_regressions"] == {}, "should still pass the immediate check"
        assert "SIMPLE" in v["baseline_tier_regressions"]
        assert v["baseline_tier_regressions"]["SIMPLE"] == pytest.approx(-0.062)

    def test_a_challenger_clean_against_both_promotes(self):
        better = result(0.80, CRITICAL=0.94, COMPLEX=0.80, MODERATE=0.70, SIMPLE=0.86)
        v = decide(self.S7, better, baseline=self.ORIGINAL)
        assert v["promote"] is True
        assert v["baseline_tier_regressions"] == {}

    def test_rejection_distinguishes_which_check_failed(self):
        """An actionable rejection: 'this step' vs 'accumulated drift' need
        different fixes, so the verdict must not conflate them."""
        v = decide(self.S7, self.GUARD, baseline=self.ORIGINAL)
        assert v["tier_regressions"] == {}
        assert v["baseline_tier_regressions"]
        assert v["baseline_checked"] is True

    def test_baseline_checked_is_false_when_none_given(self):
        v = decide(self.S7, self.GUARD)
        assert v["baseline_checked"] is False
        assert v["baseline_tier_regressions"] == {}

    def test_accuracy_below_the_pinned_baseline_rejects(self):
        """Accuracy is monotonic only if every step was gated; don't assume it."""
        weak = result(0.65, CRITICAL=0.95, COMPLEX=0.80, MODERATE=0.90, SIMPLE=0.90)
        v = decide(result(0.60, CRITICAL=0.95, COMPLEX=0.80, MODERATE=0.90, SIMPLE=0.90),
                   weak, baseline=self.ORIGINAL)
        assert v["promote"] is False
        assert v["baseline_accuracy_ok"] is False

    def test_a_tier_missing_from_the_challenger_is_a_baseline_regression_too(self):
        chal = {"accuracy": 0.99, "per_tier": {"SIMPLE": {"recall": 1.0}}}
        v = decide(self.S7, chal, baseline=self.ORIGINAL)
        assert v["promote"] is False
        assert "CRITICAL" in v["baseline_tier_regressions"]

    def test_cumulative_delta_uses_the_same_rounding_as_the_immediate_check(self):
        """The float-boundary fix must apply to both comparisons."""
        base = result(0.70, CRITICAL=0.90, COMPLEX=0.90, MODERATE=0.90, SIMPLE=0.90)
        chal = result(0.70, CRITICAL=0.90, COMPLEX=0.90, MODERATE=0.90, SIMPLE=0.85)
        assert decide(base, chal, baseline=base)["promote"] is True


class TestLoadBaseline:
    """A pinned baseline is only meaningful on the instrument it was measured
    on — comparing recall across different eval sets is nonsense, so a mismatch
    must fail loudly rather than silently produce an authoritative-looking number."""

    def _file(self, tmp_path, dataset="labeled_tasks_github.jsonl", n=176):
        p = tmp_path / "b.json"
        p.write_text(json.dumps({
            "dataset": dataset, "n": n,
            "accuracy": {"incumbent_hybrid_9b": 0.705},
            "per_tier_recall": {"incumbent_hybrid_9b": {
                "CRITICAL": 0.931, "COMPLEX": 0.763, "MODERATE": 0.417, "SIMPLE": 0.878}},
        }))
        return p

    def test_loads_the_named_config(self, tmp_path):
        b = load_baseline(self._file(tmp_path), "labeled_tasks_github.jsonl", 176)
        assert b["accuracy"] == 0.705
        assert per_tier_recall(b)["SIMPLE"] == 0.878

    def test_refuses_a_baseline_from_a_different_dataset(self, tmp_path):
        with pytest.raises(SystemExit, match="different instrument"):
            load_baseline(self._file(tmp_path, dataset="other.jsonl"),
                          "labeled_tasks_github.jsonl", 176)

    def test_refuses_a_baseline_with_a_different_row_count(self, tmp_path):
        with pytest.raises(SystemExit, match="different instrument"):
            load_baseline(self._file(tmp_path, n=42), "labeled_tasks_github.jsonl", 176)

    def test_missing_config_key_is_a_loud_error(self, tmp_path):
        with pytest.raises(SystemExit, match="nope"):
            load_baseline(self._file(tmp_path), "labeled_tasks_github.jsonl", 176, key="nope")
