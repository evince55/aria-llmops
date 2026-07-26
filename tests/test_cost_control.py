"""Tests for the cost-control layer.

The project's second stated pillar is "cost-per-task control", and measuring the
existing telemetry showed it was not merely unvalidated but UNEXERCISED: of 218
recorded route decisions, 213 (97.7%) chose the free local model, so
`estimated_usd` was 0.0 by construction, the assumed output ratio was multiplied
by zero, and the budget gate never had anything to gate. Only 3 of 218 decisions
could even be joined to an actual usage record.

That reframes what "control" means here. The number that matters for a
local-first router is not what it spent (~$0) but what local-first BOUGHT —
the counterfactual cost of the same work on a priced model. Nothing computed
that before.

Two honesty rules are pinned below, because both are easy to get wrong in a way
that flatters the result:

* coverage is reported alongside any estimate-vs-actual error, so a confident
  number is never computed from 3 joined rows;
* the counterfactual prices the ACTUAL measured tokens, never an assumed split —
  an assumption multiplied by a made-up ratio is not a measurement.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry.cost_control import (  # noqa: E402
    counterfactual, estimate_error, router_spend, savings_report,
)

RATES = {"cloud/a": {"input": 1.00, "output": 4.00},
         "local/free": {"input": 0.0, "output": 0.0}}


def usage(model="local/free", i=0, o=0, imputed=0.0, actual=0.0, task="t"):
    return {"event": "usage", "model": model, "input_tokens": i, "output_tokens": o,
            "imputed_usd": imputed, "actual_usd": actual, "task_text": task}


def decision(model="local/free", est=0.0, task="t"):
    return {"event": "route_decision", "chosen_model": model,
            "estimated_usd": est, "task_text": task}


class TestRouterSpend:
    def test_sums_actual_and_imputed_by_model(self):
        s = router_spend([usage("cloud/a", imputed=0.5, actual=0.4),
                          usage("local/free", imputed=0.0)])
        assert s["by_model"]["cloud/a"]["imputed_usd"] == pytest.approx(0.5)
        assert s["total_imputed_usd"] == pytest.approx(0.5)
        assert s["total_actual_usd"] == pytest.approx(0.4)

    def test_ignores_non_usage_events(self):
        assert router_spend([decision()])["n_events"] == 0

    def test_empty_is_safe(self):
        s = router_spend([])
        assert s["total_imputed_usd"] == 0.0 and s["n_events"] == 0


class TestCounterfactual:
    def test_prices_the_measured_tokens_not_an_assumed_split(self):
        """1M in + 1M out at $1/$4 = $5. An assumed 0.4 ratio would give $2.80 —
        the whole point is that this uses what was actually measured."""
        c = counterfactual([usage(i=1_000_000, o=1_000_000)], "cloud/a", rates=RATES)
        assert c["usd"] == pytest.approx(5.0)

    def test_reports_per_task_rate(self):
        c = counterfactual([usage(i=100, o=100), usage(i=100, o=100)], "cloud/a", rates=RATES)
        assert c["usd_per_task"] == pytest.approx(c["usd"] / 2)

    def test_unknown_model_is_a_loud_error_not_a_silent_zero(self):
        with pytest.raises(SystemExit, match="no rate"):
            counterfactual([usage(i=1, o=1)], "cloud/nope", rates=RATES)

    def test_empty_workload_does_not_divide_by_zero(self):
        c = counterfactual([], "cloud/a", rates=RATES)
        assert c["usd"] == 0.0 and c["usd_per_task"] == 0.0


class TestSavingsReport:
    def test_reports_what_local_first_bought(self):
        ev = [usage("local/free", i=1_000_000, o=1_000_000)]
        r = savings_report(ev, cloud_models=("cloud/a",), rates=RATES)
        assert r["actual_imputed_usd"] == pytest.approx(0.0)
        assert r["counterfactual"]["cloud/a"]["usd"] == pytest.approx(5.0)
        assert r["saved_usd_vs"]["cloud/a"] == pytest.approx(5.0)

    def test_saving_is_zero_when_the_work_already_ran_on_that_model(self):
        ev = [usage("cloud/a", i=1_000_000, o=1_000_000, imputed=5.0)]
        r = savings_report(ev, cloud_models=("cloud/a",), rates=RATES)
        assert r["saved_usd_vs"]["cloud/a"] == pytest.approx(0.0)


class TestEstimateError:
    """The feedback loop: was the cost model right? Coverage must be reported —
    on this repo's data only 3 of 218 decisions join to an actual."""

    def test_reports_error_and_coverage_together(self):
        e = estimate_error([decision("cloud/a", est=1.0, task="x")],
                           [usage("cloud/a", imputed=2.0, task="x")])
        assert e["n_joined"] == 1
        assert e["n_decisions"] == 1
        assert e["coverage"] == pytest.approx(1.0)
        assert e["mean_abs_error_usd"] == pytest.approx(1.0)

    def test_unjoinable_decisions_lower_coverage_rather_than_vanishing(self):
        e = estimate_error([decision(task="a"), decision(task="b")],
                           [usage(task="a")])
        assert e["n_decisions"] == 2 and e["n_joined"] == 1
        assert e["coverage"] == pytest.approx(0.5)

    def test_zero_coverage_reports_no_error_rather_than_a_fake_one(self):
        e = estimate_error([decision(task="a")], [usage(task="zzz")])
        assert e["n_joined"] == 0
        assert e["mean_abs_error_usd"] is None, "must not invent an error from no data"

    def test_flags_when_coverage_is_too_thin_to_conclude(self):
        e = estimate_error([decision(task=f"t{i}") for i in range(100)],
                           [usage(task="t0")])
        assert e["sufficient"] is False

    def test_sufficient_when_coverage_is_broad(self):
        d = [decision(task=f"t{i}", est=1.0) for i in range(20)]
        u = [usage(task=f"t{i}", imputed=1.0) for i in range(20)]
        assert estimate_error(d, u)["sufficient"] is True


class TestSavingsIsScopedToRouterTraffic:
    """Mixing ingested harness traffic with router traffic makes the saving
    meaningless: on the real ledger the Claude Code sessions cost far more than
    the same tokens would on any router model, so an unscoped report showed
    $0.00 saved and hid the actual result."""

    def test_savings_can_be_scoped_by_harness(self):
        ev = [usage("local/free", i=1_000_000, o=1_000_000), ]
        ev[0]["harness"] = "llmops-local"
        other = usage("cloud/a", i=9_000_000, o=9_000_000, imputed=999.0)
        other["harness"] = "claude-code"
        r = savings_report(ev + [other], cloud_models=("cloud/a",), rates=RATES,
                           harnesses=("llmops-local",))
        assert r["n_tasks"] == 1
        assert r["saved_usd_vs"]["cloud/a"] == pytest.approx(5.0)

    def test_unscoped_still_works_for_callers_that_want_everything(self):
        ev = [usage("local/free", i=1_000_000, o=1_000_000)]
        assert savings_report(ev, cloud_models=("cloud/a",), rates=RATES)["n_tasks"] == 1

    def test_scoping_to_an_absent_harness_is_empty_not_an_error(self):
        r = savings_report([usage(i=1, o=1)], cloud_models=("cloud/a",), rates=RATES,
                           harnesses=("nope",))
        assert r["n_tasks"] == 0 and r["saved_usd_vs"]["cloud/a"] == 0.0


class TestEvalSpendIsVisible:
    """The cost centre this project actually has.

    Discovered the hard way 2026-07-25: the router routes to FREE local models
    (~$0 routed spend) while the eval/judge loop ran the opencode-go subscription
    to 100% of its rolling limit in a day — roughly $7 across deepseek-v4-pro,
    glm-5.2 and minimax-m3, almost entirely A/B grading. `call_judge` shells out
    to `opencode run` and logged NOTHING, so the only cost that mattered was the
    only cost nobody could see.

    These are ESTIMATES from character counts, not billed figures, and must say
    so — an estimate presented as a measurement is the failure mode this whole
    module exists to avoid.
    """

    def test_judge_call_is_recorded_with_its_model(self):
        from telemetry.cost_control import judge_event
        e = judge_event("opencode-go/glm-5.2", "prompt text", "reply text")
        assert e["event"] == "usage"
        assert e["model"] == "opencode-go/glm-5.2"
        assert e["harness"] == "llmops-judge"

    def test_tokens_are_estimated_and_labelled_as_estimated(self):
        from telemetry.cost_control import judge_event
        e = judge_event("opencode-go/glm-5.2", "x" * 400, "y" * 200)
        assert e["input_tokens"] > 0 and e["output_tokens"] > 0
        assert e["cost_model"] == "estimated-from-chars", \
            "an estimate must not be presentable as a measurement"

    def test_eval_spend_is_separable_from_routed_spend(self):
        from telemetry.cost_control import eval_spend
        ev = [usage("local/free", i=10, o=10), ]
        ev[0]["harness"] = "llmops-local"
        j = usage("cloud/a", i=1000, o=1000, imputed=5.0)
        j["harness"] = "llmops-judge"
        s = eval_spend(ev + [j])
        assert s["n_judge_calls"] == 1
        assert s["imputed_usd"] == pytest.approx(5.0)

    def test_eval_spend_empty_is_safe(self):
        from telemetry.cost_control import eval_spend
        assert eval_spend([])["n_judge_calls"] == 0


class TestUnpricedModelsAreLoudNotZero:
    """The graders that exhausted the subscription — opencode-go/glm-5.2 and
    deepseek-v4-pro — are absent from MODEL_RATES, so a naive lookup prices the
    single most expensive activity in the project at $0.00. A silent zero on the
    spend that actually hurts is the worst possible failure for a cost module."""

    def test_an_unpriced_model_is_flagged_not_silently_free(self):
        from telemetry.cost_control import judge_event
        e = judge_event("opencode-go/glm-5.2", "p" * 4000, "r" * 800, rates=RATES)
        assert e["unpriced"] is True
        assert e["imputed_usd"] == 0.0

    def test_a_priced_model_is_not_flagged(self):
        from telemetry.cost_control import judge_event
        e = judge_event("cloud/a", "p" * 4000, "r" * 800, rates=RATES)
        assert e["unpriced"] is False and e["imputed_usd"] > 0

    def test_eval_spend_surfaces_unpriced_call_count(self):
        from telemetry.cost_control import eval_spend, judge_event
        ev = [judge_event("opencode-go/glm-5.2", "p", "r", rates=RATES),
              judge_event("cloud/a", "p" * 4000, "r", rates=RATES)]
        s = eval_spend(ev)
        assert s["n_unpriced_calls"] == 1
        assert "unpriced" in s["note"].lower()
