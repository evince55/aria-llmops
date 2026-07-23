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
    TIER_TOLERANCE, decide, load_rows, per_tier_recall,
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
