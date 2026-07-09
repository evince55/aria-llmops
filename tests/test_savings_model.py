"""The savings model's math and its honesty contract.

The calculator is a sales artifact; its credibility rests on (a) arithmetic
that survives adversarial checks and (b) every input carrying provenance.
Both are pinned here — including the model's willingness to recommend AGAINST
the fancy option (local box) when the client's volume doesn't justify it.
"""
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from calculator.savings_model import Params, compute


def _p(**over):
    return Params(**{**asdict(Params()), **over})


# ---- arithmetic identities ---------------------------------------------------

def test_human_baseline_is_volume_times_task_cost():
    p = _p(tasks_per_month=1000, minutes_per_task_human=6, loaded_hourly_usd=60)
    res = compute(p, use_measured=False)
    assert res["monthly_usd"]["human_baseline"] == 1000 * 6.0  # 6 min @ $60/h = $6


def test_zero_volume():
    res = compute(_p(tasks_per_month=0), use_measured=False)
    m = res["monthly_usd"]
    assert m["human_baseline"] == 0.0
    assert m["naive_ai"] == 0.0
    assert m["routed_cloud_only"] == 0.0
    assert m["routed_local_box"] == _p().local_infra_usd_month  # box still costs
    assert res["recommended_configuration"] == "cloud-only"     # obviously


def test_recommended_routed_beats_naive_under_defaults():
    res = compute(Params())
    m = res["monthly_usd"]
    assert m["routed_recommended"] < m["naive_ai"]


def test_box_break_even_exists_and_recommendation_flips_around_it():
    # The business insight the model must not hide: a $200/mo box loses to
    # dirt-cheap cloud rates at small volume and only wins at real scale.
    res = compute(Params(), use_measured=False)
    be = res["local_box_break_even_tasks_per_month"]
    assert be is not None and be > 0
    below = compute(_p(tasks_per_month=max(1, int(be * 0.5))), use_measured=False)
    above = compute(_p(tasks_per_month=int(be * 2)), use_measured=False)
    assert below["recommended_configuration"] == "cloud-only"
    assert above["recommended_configuration"] == "local-box"


def test_frontier_retry_policy_costs_more_than_cheap_retry():
    cheap = compute(_p(local_retry_target="cheap"), use_measured=False)
    frontier = compute(_p(local_retry_target="frontier"), use_measured=False)
    assert (frontier["monthly_usd"]["routed_local_box"]
            > cheap["monthly_usd"]["routed_local_box"])


def test_perfect_local_success_zeroes_local_tier_spend_in_box_variant():
    p = _p(local_success_rate=1.0,
           tier_executor={"SIMPLE": "local", "MODERATE": "local",
                          "COMPLEX": "local", "CRITICAL": "local"})
    res = compute(p, use_measured=False)
    tiers = res["monthly_usd"]["routed_breakdown"]["token_spend_by_tier"]
    assert all(t["usd_local_box"] == 0.0 for t in tiers.values())


def test_lower_success_rate_costs_strictly_more_in_box_variant():
    lo = compute(_p(local_success_rate=0.6), use_measured=False)
    hi = compute(_p(local_success_rate=0.95), use_measured=False)
    assert lo["monthly_usd"]["routed_local_box"] > hi["monthly_usd"]["routed_local_box"]


def test_net_savings_are_net_of_service_fee():
    free = compute(_p(service_fee_usd_month=0.0), use_measured=False)[
        "client_net_savings_usd_month"]["vs_human_baseline"]
    paid = compute(_p(service_fee_usd_month=500.0), use_measured=False)[
        "client_net_savings_usd_month"]["vs_human_baseline"]
    assert round(free - paid, 2) == 500.0


def test_payback_none_when_savings_nonpositive():
    res = compute(_p(tasks_per_month=0), use_measured=False)
    assert res["payback_months_on_setup_fee"] is None


def test_calls_per_task_scales_token_costs_linearly():
    one = compute(_p(calls_per_task=1), use_measured=False)["per_task_usd"]["frontier_tokens"]
    four = compute(_p(calls_per_task=4), use_measured=False)["per_task_usd"]["frontier_tokens"]
    assert round(four / one, 6) == 4.0


# ---- honesty contract ----------------------------------------------------------

def test_every_input_has_a_provenance_tag():
    res = compute(Params())
    prov = res["inputs"]["_provenance"]
    for k in asdict(Params()):
        assert k in prov, f"input {k} lacks a provenance tag"


def test_honesty_notes_present_and_mention_small_n():
    res = compute(Params())
    text = " ".join(res["honesty"]).lower()
    assert "small n" in text
    assert "assumption" in text


def test_automatable_fraction_keeps_humans_in_every_scenario():
    res = compute(_p(tasks_per_month=1000, automatable_fraction=0.7), use_measured=False)
    assert res["monthly_usd"]["routed_breakdown"]["still_manual_tasks"] > 0


# ---- sensitivity + CLI ----------------------------------------------------------

def test_sensitivity_scales_with_volume():
    res = compute(Params())
    rows = res["sensitivity"]
    assert [r["tasks_per_month"] for r in rows] == sorted(r["tasks_per_month"] for r in rows)
    assert rows[-1]["net_vs_human"] > rows[0]["net_vs_human"]


def test_cli_json_roundtrip():
    out = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "calculator" / "savings_model.py"),
         "--tasks-per-month", "500", "--json"],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    assert res["inputs"]["tasks_per_month"] == 500
    assert "client_net_savings_usd_month" in res
    assert "recommended_configuration" in res


def test_cli_local_success_override_beats_measured():
    out = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "calculator" / "savings_model.py"),
         "--local-success", "0.5", "--json"],
        capture_output=True, text=True)
    res = json.loads(out.stdout)
    assert res["inputs"]["local_success_rate"] == 0.5
