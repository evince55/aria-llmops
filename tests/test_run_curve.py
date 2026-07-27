"""Decision logic for the data-efficiency curve.

Both rules are pre-registered
(docs/research/2026-07-26-data-curve-preregistration.md) and both are computed
rather than eyeballed, because the outcome I *want* — an early plateau, which
would contradict the paper's stated data requirement — is exactly the outcome a
generous reading of a noisy curve would hand me.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.run_curve import verdict, void_check  # noqa: E402


class TestVoidCheck:
    def test_a_reproduced_baseline_is_not_void(self):
        assert void_check(0.820)["void"] is False

    def test_small_drift_is_tolerated(self):
        assert void_check(0.79)["void"] is False

    def test_drift_past_tolerance_voids_the_curve(self):
        # The generator moved; every other point measures that, not data scale.
        assert void_check(0.70)["void"] is True

    def test_drift_upward_voids_it_too(self):
        # A baseline that improved is just as much evidence the data changed.
        assert void_check(0.95)["void"] is True

    def test_it_reports_the_drift_it_measured(self):
        assert void_check(0.77)["drift"] == 0.05


class TestVerdict:
    BASE = {"460": 0.820}

    def test_a_small_gain_is_a_plateau(self):
        v = verdict({**self.BASE, "10000": 0.84})
        assert v["verdict"] == "PLATEAU" and v["gain_over_baseline"] == 0.02

    def test_a_large_gain_is_scaling(self):
        assert verdict({**self.BASE, "10000": 0.92})["verdict"] == "SCALING"

    def test_the_threshold_boundary_counts_as_scaling(self):
        assert verdict({**self.BASE, "10000": 0.87})["verdict"] == "SCALING"

    def test_the_best_arm_wins_even_if_it_is_not_the_largest(self):
        # More data need not be monotone; the rule asks whether ANY larger size
        # helped, not whether the largest did.
        v = verdict({**self.BASE, "2500": 0.91, "10000": 0.83})
        assert v["best_size"] == 2500 and v["verdict"] == "SCALING"

    def test_arms_below_the_baseline_still_yield_a_plateau_not_an_error(self):
        v = verdict({**self.BASE, "10000": 0.60})
        assert v["verdict"] == "PLATEAU" and v["gain_over_baseline"] < 0

    def test_a_curve_with_only_the_baseline_is_incomplete_not_a_plateau(self):
        assert verdict(self.BASE)["verdict"] == "INCOMPLETE"
