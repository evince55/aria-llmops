"""The circuit breaker: a cost model that can see the spend but not stop it.

Finding 11 said a cost model that cannot see the line item exhausting your
budget is not a cost model. This project then built that visibility — and still
had no way to stop anything. The A2/A2b grading runs consumed a monthly
subscription's rolling limit in a day, and every part of the system watched it
happen and reported it accurately afterwards.

Note what would NOT have caught it: a per-task cost cap. No single judge call was
expensive; there were hundreds of cheap ones. The guard therefore scopes a
BUDGET TO A RUN, not to a call.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry.spend_guard import BudgetExhausted, SpendGuard, Unpriceable  # noqa: E402


class TestBudgetIsExplicit:
    def test_a_budget_is_required(self):
        # Findings 12, 19 and 23 were all unnamed defaults. A guard with a
        # default budget is a guard nobody decided the value of.
        with pytest.raises(ValueError):
            SpendGuard(name="evals", budget_usd=None)

    def test_a_negative_budget_is_refused_not_clamped(self):
        with pytest.raises(ValueError):
            SpendGuard(name="evals", budget_usd=-1.0)

    def test_a_zero_budget_stops_the_first_call(self):
        g = SpendGuard(name="evals", budget_usd=0.0)
        with pytest.raises(BudgetExhausted):
            g.charge(0.01)


class TestItStopsBeforeSpendingNotAfter:
    def test_calls_under_budget_pass(self):
        g = SpendGuard(name="evals", budget_usd=1.00)
        for _ in range(5):
            g.charge(0.10)
        assert g.spent == pytest.approx(0.50) and g.remaining == pytest.approx(0.50)

    def test_the_call_that_would_exceed_is_refused_before_it_runs(self):
        # Checking after the fact means the overspend already happened.
        g = SpendGuard(name="evals", budget_usd=1.00)
        g.charge(0.90)
        with pytest.raises(BudgetExhausted):
            g.charge(0.20)
        assert g.spent == pytest.approx(0.90), "a refused call must not be charged"

    def test_a_call_that_exactly_fills_the_budget_is_allowed(self):
        g = SpendGuard(name="evals", budget_usd=1.00)
        g.charge(1.00)
        assert g.remaining == pytest.approx(0.0)


class TestItFailsClosed:
    def test_an_unpriceable_call_is_refused_not_treated_as_free(self):
        # cost_control already flags unpriced models rather than zeroing them.
        # Enforcement must inherit that: $0 for "I don't know" is how an
        # unmetered loop looks exactly like a free one.
        g = SpendGuard(name="evals", budget_usd=1.00)
        with pytest.raises(Unpriceable):
            g.charge(None)

    def test_an_unpriceable_call_does_not_move_the_meter(self):
        g = SpendGuard(name="evals", budget_usd=1.00)
        with pytest.raises(Unpriceable):
            g.charge(None)
        assert g.spent == 0.0


class TestOnceTrippedItStaysTripped:
    def test_a_tripped_guard_refuses_even_an_affordable_call(self):
        # Otherwise a run limps on, spending in small increments, which is the
        # exact shape of the incident this exists to prevent.
        g = SpendGuard(name="evals", budget_usd=1.00)
        g.charge(0.99)
        with pytest.raises(BudgetExhausted):
            g.charge(0.50)
        with pytest.raises(BudgetExhausted):
            g.charge(0.001)

    def test_it_reports_why_it_tripped(self):
        g = SpendGuard(name="evals", budget_usd=1.00)
        g.charge(0.60)
        with pytest.raises(BudgetExhausted) as exc:
            g.charge(0.60)
        msg = str(exc.value)
        assert "evals" in msg and "0.60" in msg and "1.00" in msg

    def test_the_report_survives_the_trip(self):
        g = SpendGuard(name="evals", budget_usd=1.00)
        g.charge(0.60)
        with pytest.raises(BudgetExhausted):
            g.charge(0.60)
        r = g.report()
        assert r["tripped"] and r["calls"] == 1 and r["spent"] == pytest.approx(0.60)


class TestItCountsCallsToo:
    def test_a_call_cap_stops_a_loop_of_cheap_calls(self):
        # The incident was hundreds of CHEAP calls. A dollar budget alone can be
        # defeated by a loop that never reaches it but never terminates either.
        g = SpendGuard(name="evals", budget_usd=1000.0, max_calls=3)
        for _ in range(3):
            g.charge(0.001)
        with pytest.raises(BudgetExhausted):
            g.charge(0.001)

    def test_the_call_cap_is_optional(self):
        g = SpendGuard(name="evals", budget_usd=1.0)
        for _ in range(50):
            g.charge(0.001)
        assert g.report()["calls"] == 50


class TestItIsActuallyWiredIntoTheLoopThatSpends:
    """A guard nothing calls is a guard that does not exist.

    Finding 11 was about instrumenting the loop the money actually goes to
    rather than the one the architecture is about. The same applies to
    enforcement: a breaker on the routing loop would be architecturally tidy and
    operationally useless, because routed inference runs on free local models
    while the eval loop is what consumed the subscription.
    """

    def _rows(self, n):
        return [{"task": f"task {i}", "tier": "SIMPLE"} for i in range(n)]

    def test_an_over_budget_judging_run_stops_early(self, monkeypatch):
        import evals.judge_labels as jl
        calls = {"n": 0}

        def fake_call(model, prompt, cwd, guard=None):
            if guard is not None:
                guard.charge(0.40)          # each batch costs 0.40
            calls["n"] += 1
            return '[{"i": 0, "tier": "SIMPLE"}]'

        monkeypatch.setattr(jl, "call_judge", fake_call)
        guard = SpendGuard(name="test", budget_usd=1.00)
        jl.judge_rows(self._rows(20), models=("opencode-go/x",), batch_size=1,
                      guard=guard)
        # 2 batches fit in $1.00; the third would exceed and stops the run.
        assert calls["n"] == 2 and guard.tripped

    def test_partial_results_survive_the_trip(self, monkeypatch):
        # The spend already happened. Discarding what it bought would make the
        # guard cost money rather than save it.
        import evals.judge_labels as jl

        def fake_call(model, prompt, cwd, guard=None):
            if guard is not None:
                guard.charge(0.40)
            return '[{"i": 0, "tier": "SIMPLE"}]'

        monkeypatch.setattr(jl, "call_judge", fake_call)
        res = jl.judge_rows(self._rows(20), models=("opencode-go/x",), batch_size=1,
                            guard=SpendGuard(name="test", budget_usd=1.00))
        assert isinstance(res, dict) and "kept" in res

    def test_an_unguarded_run_is_unchanged(self, monkeypatch):
        # The guard is opt-in; omitting it must not alter existing behaviour.
        import evals.judge_labels as jl
        calls = {"n": 0}

        def fake_call(model, prompt, cwd, guard=None):
            calls["n"] += 1
            return '[{"i": 0, "tier": "SIMPLE"}]'

        monkeypatch.setattr(jl, "call_judge", fake_call)
        jl.judge_rows(self._rows(5), models=("opencode-go/x",), batch_size=1)
        assert calls["n"] == 5


class TestArgHelpers:
    """One place to get the wiring right, rather than five.

    Five scripts spend through two paid-call primitives. Hand-rolling the same
    three lines in each is five chances to omit one — and the omission is
    invisible until a run costs money.
    """

    def _parse(self, argv):
        import argparse
        from telemetry.spend_guard import add_budget_args
        p = argparse.ArgumentParser()
        add_budget_args(p)
        return p.parse_args(argv)

    def test_no_flags_means_no_guard(self):
        from telemetry.spend_guard import guard_from_args
        assert guard_from_args(self._parse([]), name="x") is None

    def test_a_budget_builds_a_guard(self):
        from telemetry.spend_guard import guard_from_args
        g = guard_from_args(self._parse(["--budget-usd", "2.50"]), name="x")
        assert g is not None and g.budget == 2.50 and g.name == "x"

    def test_a_call_cap_alone_still_builds_a_guard(self):
        # A cap on calls is a real limit even without a dollar ceiling; the
        # guard requires a budget, so an unbounded one is supplied explicitly.
        from telemetry.spend_guard import guard_from_args
        g = guard_from_args(self._parse(["--max-calls", "10"]), name="x")
        assert g is not None and g.max_calls == 10 and g.budget == float("inf")

    def test_both_flags_compose(self):
        from telemetry.spend_guard import guard_from_args
        g = guard_from_args(self._parse(["--budget-usd", "1", "--max-calls", "3"]), name="x")
        assert g.budget == 1.0 and g.max_calls == 3

    def test_the_help_text_says_what_happens_without_one(self):
        # The guard is opt-in, so the CLI is the only place a user learns that
        # omitting it means unlimited spend.
        import argparse
        from telemetry.spend_guard import add_budget_args
        p = argparse.ArgumentParser()
        add_budget_args(p)
        help_text = p.format_help()
        assert "unguarded" in help_text.lower() or "no limit" in help_text.lower()
