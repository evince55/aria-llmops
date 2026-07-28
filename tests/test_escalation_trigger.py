"""The router's escalation-trigger gate.

Both criteria are pre-registered and both are computed, because each is trivial
to satisfy alone and useless alone: abstain on everything for perfect precision,
accept everything for perfect coverage. The gate exists to stop either being
reported as a success.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.escalation_trigger import gate, run  # noqa: E402


class TestGate:
    def test_safe_and_useful_ships(self):
        assert gate({"precision_on_covered": 0.95, "coverage": 0.6})["ship"] is True

    def test_safe_but_rarely_covering_does_not_ship(self):
        # A trigger that escalates 80% of traffic is a tax with extra steps.
        g = gate({"precision_on_covered": 1.0, "coverage": 0.2})
        assert g["ship"] is False and g["safe_enough"] and not g["covers_enough"]

    def test_broad_but_unsafe_does_not_ship(self):
        # Covered rows ship WITHOUT escalation, so they have to be right.
        g = gate({"precision_on_covered": 0.78, "coverage": 0.95})
        assert g["ship"] is False and g["covers_enough"] and not g["safe_enough"]

    def test_abstaining_on_everything_is_not_a_pass(self):
        # precision_on_covered is None when nothing was covered.
        assert gate({"precision_on_covered": None, "coverage": 0.0})["ship"] is False

    def test_thresholds_are_reported_with_the_verdict(self):
        g = gate({"precision_on_covered": 0.9, "coverage": 0.5})
        assert g["min_precision"] == 0.90 and g["min_coverage"] == 0.50 and g["ship"]


class TestRun:
    TASKS = [{"task": "a", "truth": "SIMPLE"}, {"task": "b", "truth": "COMPLEX"},
             {"task": "c", "truth": "CRITICAL"}]

    def test_agreement_is_scored_and_disagreement_escalates(self):
        res = run(self.TASKS, lambda t: {"a": "SIMPLE", "b": "COMPLEX", "c": "SIMPLE"}[t],
                  lambda t: {"a": "SIMPLE", "b": "COMPLEX", "c": "CRITICAL"}[t])
        s = res["summary"]
        assert s["coverage"] == 2/3 and s["precision_on_covered"] == 1.0

    def test_agreeing_on_a_wrong_answer_counts_against_precision(self):
        # Two correlated arms can agree and both be wrong; that is the failure
        # the gate's precision criterion exists to catch.
        res = run(self.TASKS, lambda t: "SIMPLE", lambda t: "SIMPLE")
        assert res["summary"]["coverage"] == 1.0
        assert res["summary"]["precision_on_covered"] == 1/3

    def test_an_arm_returning_none_escalates_that_row(self):
        res = run(self.TASKS, lambda t: None, lambda t: "SIMPLE")
        assert res["summary"]["coverage"] == 0.0

    def test_per_tier_precision_is_reported(self):
        # An aggregate can look fine while being concentrated in one tier, and
        # shipping a wrong CRITICAL is not the same cost as a wrong SIMPLE.
        res = run(self.TASKS, lambda t: "SIMPLE", lambda t: "SIMPLE")
        by = res["summary"]["covered_precision_by_tier"]
        assert by["SIMPLE"]["precision"] == 1.0 and by["CRITICAL"]["precision"] == 0.0


class TestTheHarnessDoesNotInventAbstentions:
    """A bare `except` in a measurement harness turns a bug into a data point.

    The first run of this trigger reported coverage 0.0 across all 176 rows and
    it looked like a finding. It was an AttributeError — I guessed the classifier
    API — swallowed by `except Exception: return None`, which the scorer then
    read as "the model declined to answer" 176 times.
    """

    def test_a_programming_error_in_an_arm_is_raised_not_scored(self):
        import pytest
        def broken(_task):
            raise AttributeError("no such method")
        with pytest.raises(AttributeError):
            run([{"task": "a", "truth": "SIMPLE"}], lambda t: "SIMPLE", broken)

    def test_a_genuine_abstention_is_still_scored_as_one(self):
        res = run([{"task": "a", "truth": "SIMPLE"}], lambda t: "SIMPLE", lambda t: None)
        assert res["summary"]["coverage"] == 0.0
