#!/usr/bin/env python3
"""Savings model: what routed, token-cost-conscious automation is worth to a business.

WHY THIS EXISTS
---------------
This repo measures one system doing cost-aware routing on real usage. The
business question a prospect actually asks is different: *"for MY volume of
repetitive tasks, what does this save me, after paying you?"* This module turns
that question into arithmetic with every assumption on the table.

THE THREE WORLDS IT COMPARES (monthly, for one task stream)
-----------------------------------------------------------
1. human_baseline — people do all of it (what most prospects run today).
2. naive_ai       — the automatable slice runs on a frontier model for
                    everything (the "wrapper agency" competitor / DIY default).
3. routed_ai      — the offering: classify each task, run it on the cheapest
                    capable tier (self-hosted local model for the bulk, cheap
                    cloud where needed, frontier only where warranted), with
                    failures retried on frontier and a human QA slice priced in.

Savings vs (1) sells automation at all; savings vs (2) sells THIS service.
Client-side numbers are always net of the service's own fees — the pitch
number is what the CLIENT keeps.

HONESTY RULES (the calculator's sales value IS its credibility)
---------------------------------------------------------------
- Every input carries a provenance tag: "measured" (this repo's live run),
  "list-rate" (published API pricing), or "assumption" (overridable default).
- Measured numbers come from a 12-task live run on one box — evidence, not
  statistics. The output says so.
- Failures are not free: local-tier failures pay the local attempt AND a
  frontier retry. QA humans are not free: a review slice is always costed.
- No task stream is 100% automatable: `automatable_fraction` defaults to 0.7
  and the rest stays human in EVERY scenario.

Standard library only. `python3 calculator/savings_model.py --help` to run.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry import pricing  # noqa: E402  (list rates: single source of truth)

# ---------------------------------------------------------------------------
# Measured reference points (this repo's own live run — see
# evals/live-runs/results.json). Small N; these seed DEFAULTS, nothing more.
# ---------------------------------------------------------------------------
MEASURED = {
    # 2026-07-09 live routing run (evals/live-runs/results.json): 12 labeled
    # tasks routed by the production hybrid classifier; 11 executed on the
    # local 35B (the CRITICAL task correctly routed to cloud); outcomes graded
    # via reviewer reactions -> keyword/9B pipeline. 8/11 succeeded. Two of the
    # three failures trace to the 800-token output cap, not model capability —
    # treat this as a FLOOR for a properly configured deployment.
    "local_success_rate": 0.727,
    "n_local_executions": 11,
    # Reference latencies from the same run (seconds, includes llama-swap
    # model swap-in where it occurred): local 35B execution wall 73.7-143.8
    # (mean 116.5) at <=800 output tokens; 9B tier classify 2.4 resident,
    # ~14.1 when swapped in.
    "local_exec_wall_s_mean": 116.5,
    "classifier_resident_s": 2.4,
    "classifier_swap_in_s": 14.1,
}

# List rates (USD / 1M tokens) — from telemetry/pricing.py so the calculator
# can never drift from the ledger's own accounting.
_FRONTIER = pricing.PRICING["claude-opus-4-8"]          # the "do everything here" default
_CHEAP = pricing.PRICING["opencode/deepseek-v4-flash"]  # cheap-cloud tier


@dataclass
class Params:
    """Every knob in the model. Defaults are labeled by provenance in
    `provenance()`; override any of them per engagement."""

    # -- the client's task stream (assumptions until discovery) -------------
    tasks_per_month: int = 2000
    minutes_per_task_human: float = 6.0     # how long a person takes today
    loaded_hourly_usd: float = 35.0         # fully-loaded labor cost
    # Tokens are PER MODEL CALL; an automated business task is usually an
    # agentic pipeline (read -> extract -> act -> reply), i.e. several calls.
    tokens_in_per_call: int = 1500
    tokens_out_per_call: int = 600
    calls_per_task: int = 4
    automatable_fraction: float = 0.70      # the rest stays human in ALL scenarios

    # -- how the routed system behaves ---------------------------------------
    # Tier mix of a repetitive back-office stream (assumption; per-engagement
    # discovery replaces this) and where each tier executes (mirrors the
    # repo's TIER_PREFERENCE lead models).
    tier_mix: dict = field(default_factory=lambda: {
        "SIMPLE": 0.55, "MODERATE": 0.25, "COMPLEX": 0.15, "CRITICAL": 0.05})
    tier_executor: dict = field(default_factory=lambda: {
        "SIMPLE": "local", "MODERATE": "local", "COMPLEX": "local",
        "CRITICAL": "frontier"})
    local_success_rate: float = 0.85        # overridden by MEASURED when present
    # Where a failed local attempt retries. "cheap" mirrors the repo's actual
    # TIER_PREFERENCE fallback chains (local -> deepseek/minimax, never
    # straight to Opus); "frontier" models a maximally cautious retry policy.
    local_retry_target: str = "cheap"
    human_review_fraction: float = 0.10     # QA humans double-check this slice
    review_minutes_per_task: float = 1.5

    # -- rates (USD / 1M tokens; list rates by default) -----------------------
    frontier_in: float = _FRONTIER["input"]
    frontier_out: float = _FRONTIER["output"]
    cheap_in: float = _CHEAP["input"]
    cheap_out: float = _CHEAP["output"]
    # local marginal token cost is 0; the box is paid for by:
    local_infra_usd_month: float = 200.0    # amortized GPU box / rented server

    # -- the service's own pricing (so client numbers are NET) ----------------
    setup_fee_usd: float = 3000.0
    service_fee_usd_month: float = 500.0

    def provenance(self) -> dict:
        """Tag every default's origin so no number looks more solid than it is."""
        tags = {
            "tasks_per_month": "assumption (discovery replaces)",
            "minutes_per_task_human": "assumption (discovery replaces)",
            "loaded_hourly_usd": "assumption (discovery replaces)",
            "tokens_in_per_call": "assumption",
            "tokens_out_per_call": "assumption",
            "calls_per_task": "assumption (agentic pipeline of several calls)",
            "automatable_fraction": "assumption (conservative)",
            "tier_mix": "assumption (repetitive back-office skew)",
            "tier_executor": "mirrors repo TIER_PREFERENCE",
            "local_retry_target": "mirrors repo TIER_PREFERENCE fallback chains",
            "local_success_rate": "assumption",
            "human_review_fraction": "assumption (QA policy)",
            "review_minutes_per_task": "assumption",
            "frontier_in": "list-rate (claude-opus-4-8)",
            "frontier_out": "list-rate (claude-opus-4-8)",
            "cheap_in": "list-rate (deepseek-v4-flash)",
            "cheap_out": "list-rate (deepseek-v4-flash)",
            "local_infra_usd_month": "assumption (amortized box)",
            "setup_fee_usd": "service pricing (example)",
            "service_fee_usd_month": "service pricing (example)",
        }
        if "local_success_rate" in MEASURED:
            tags["local_success_rate"] = (
                f"measured (live run, n={MEASURED.get('n_local_executions', '?')}"
                " — small N, one workload)")
        return tags


def _token_cost(tokens_in: int, tokens_out: int, rate_in: float, rate_out: float) -> float:
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000


def _scenarios(p: Params) -> dict:
    """The monthly arithmetic for every world. One place, used by the headline
    numbers and the sensitivity sweep alike."""
    human_task = p.minutes_per_task_human / 60 * p.loaded_hourly_usd
    per_call = dict(fr=_token_cost(p.tokens_in_per_call, p.tokens_out_per_call,
                                   p.frontier_in, p.frontier_out),
                    ch=_token_cost(p.tokens_in_per_call, p.tokens_out_per_call,
                                   p.cheap_in, p.cheap_out))
    frontier_task = per_call["fr"] * p.calls_per_task
    cheap_task = per_call["ch"] * p.calls_per_task
    review_task = p.review_minutes_per_task / 60 * p.loaded_hourly_usd

    v = p.tasks_per_month
    auto = v * p.automatable_fraction
    manual_usd = (v - auto) * human_task
    qa_usd = auto * p.human_review_fraction * review_task

    human_baseline = v * human_task
    naive_ai = auto * frontier_task + qa_usd + manual_usd

    # Routed, variant A: local box for local tiers (failures retry on frontier).
    # Routed, variant B: cloud-only — local tiers run on the cheap cloud model,
    # no box to amortize. The model RECOMMENDS whichever is cheaper: an honest
    # calculator must be able to say "at your volume, skip the box".
    retry_task = frontier_task if p.local_retry_target == "frontier" else cheap_task
    tier_breakdown = {}
    tokens_local_variant = 0.0
    tokens_cloud_variant = 0.0
    for tier, share in p.tier_mix.items():
        n = auto * share
        ex = p.tier_executor.get(tier, "frontier")
        if ex == "local":
            cost_a = n * (1 - p.local_success_rate) * retry_task
            cost_b = n * cheap_task
        elif ex == "cheap":
            cost_a = cost_b = n * cheap_task
        else:
            cost_a = cost_b = n * frontier_task
        tokens_local_variant += cost_a
        tokens_cloud_variant += cost_b
        tier_breakdown[tier] = {"tasks": round(n, 1), "executor": ex,
                                "usd_local_box": round(cost_a, 2),
                                "usd_cloud_only": round(cost_b, 2)}

    routed_local_box = tokens_local_variant + p.local_infra_usd_month + qa_usd + manual_usd
    routed_cloud_only = tokens_cloud_variant + qa_usd + manual_usd

    # Volume where the box starts paying for itself: per local-tier task the
    # box saves (cheap-cloud cost) - (failure-retry cost); it wins once that
    # covers the monthly infra.
    per_task_box_saving = cheap_task - (1 - p.local_success_rate) * retry_task
    local_share = sum(s for t, s in p.tier_mix.items()
                      if p.tier_executor.get(t, "frontier") == "local")
    denom = per_task_box_saving * p.automatable_fraction * local_share
    box_break_even_tasks = (p.local_infra_usd_month / denom) if denom > 0 else None

    return {
        "human": human_baseline, "naive": naive_ai,
        "routed_local_box": routed_local_box, "routed_cloud_only": routed_cloud_only,
        "human_task": human_task, "frontier_task": frontier_task,
        "cheap_task": cheap_task, "review_task": review_task,
        "qa_usd": qa_usd, "manual_usd": manual_usd,
        "tier_breakdown": tier_breakdown,
        "box_break_even_tasks": box_break_even_tasks,
    }


def compute(p: Params, use_measured: bool = True) -> dict:
    """Return the full model output: per-task economics, the four monthly
    worlds, a recommendation, client-net savings, payback, sensitivity, and
    honesty notes.

    `use_measured=True` (default) lets this repo's measured numbers override
    the matching assumption defaults; pass False to run pure-assumption
    (e.g. when the caller supplied their own client-specific value)."""
    if use_measured and "local_success_rate" in MEASURED:
        p = Params(**{**asdict(p), "local_success_rate": MEASURED["local_success_rate"]})

    s = _scenarios(p)
    use_box = s["routed_local_box"] <= s["routed_cloud_only"]
    routed_ai = min(s["routed_local_box"], s["routed_cloud_only"])
    recommended = "local-box" if use_box else "cloud-only"

    human_baseline, naive_ai = s["human"], s["naive"]
    fees = p.service_fee_usd_month
    net_vs_human = human_baseline - routed_ai - fees
    net_vs_naive = naive_ai - routed_ai - fees
    payback = (p.setup_fee_usd / net_vs_human) if net_vs_human > 0 else None

    def _sens(mult: float) -> dict:
        q = Params(**{**asdict(p), "tasks_per_month": int(p.tasks_per_month * mult)})
        r = _scenarios(q)
        routed = min(r["routed_local_box"], r["routed_cloud_only"])
        return {"tasks_per_month": q.tasks_per_month,
                "recommended": "local-box" if r["routed_local_box"] <= r["routed_cloud_only"] else "cloud-only",
                "net_vs_human": round(r["human"] - routed - fees, 2),
                "net_vs_naive": round(r["naive"] - routed - fees, 2)}

    box_be = s["box_break_even_tasks"]
    out = {
        "inputs": {**asdict(p), "_provenance": p.provenance()},
        "per_task_usd": {
            "human": round(s["human_task"], 4),
            "frontier_tokens": round(s["frontier_task"], 6),
            "cheap_tokens": round(s["cheap_task"], 6),
            "local_marginal": 0.0,
            "qa_review": round(s["review_task"], 4),
        },
        "monthly_usd": {
            "human_baseline": round(human_baseline, 2),
            "naive_ai": round(naive_ai, 2),
            "routed_local_box": round(s["routed_local_box"], 2),
            "routed_cloud_only": round(s["routed_cloud_only"], 2),
            "routed_recommended": round(routed_ai, 2),
            "routed_breakdown": {
                "token_spend_by_tier": s["tier_breakdown"],
                "local_infra_if_box": p.local_infra_usd_month,
                "human_qa": round(s["qa_usd"], 2),
                "still_manual_tasks": round(s["manual_usd"], 2),
            },
            "service_fee": fees,
        },
        "recommended_configuration": recommended,
        "local_box_break_even_tasks_per_month": round(box_be) if box_be else None,
        "client_net_savings_usd_month": {
            "vs_human_baseline": round(net_vs_human, 2),
            "vs_naive_ai": round(net_vs_naive, 2),
            "note": "net of the monthly service fee — what the client keeps; "
                    "uses the recommended (cheaper) routed configuration",
        },
        "payback_months_on_setup_fee": round(payback, 1) if payback else None,
        "sensitivity": [_sens(m) for m in (0.5, 1, 2, 5)],
        "honesty": [
            "Measured inputs come from a 12-task live run on one box (small N).",
            "tier_mix, calls_per_task and token counts are assumptions until "
            "per-client discovery.",
            "Local-tier failures are costed as a frontier retry; retries are "
            "assumed to succeed. Cheap-cloud calls are assumed to succeed "
            "(their failure handling rides the same QA slice).",
            f"{int((1 - Params().automatable_fraction) * 100)}% of tasks stay "
            "human in every scenario — automation never claims the whole stream.",
            "Rates are list rates; negotiated/enterprise rates change the answer.",
            "When the local box loses to cloud-only at your volume, the "
            "calculator says so (recommended_configuration) — the box is an "
            "optimization for scale, not a default.",
            "The box also buys things this model does NOT price: data never "
            "leaves the premises, no per-token rate limits, latency control. "
            "For privacy-sensitive clients the box can be the right call even "
            "below its cost break-even.",
        ],
    }
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _fmt_table(res: dict) -> str:
    m = res["monthly_usd"]
    s = res["client_net_savings_usd_month"]
    be = res["local_box_break_even_tasks_per_month"]
    lines = [
        "=== Monthly cost, four worlds ===",
        f"  people do everything        ${m['human_baseline']:>10,.2f}",
        f"  naive AI (all-frontier)     ${m['naive_ai']:>10,.2f}",
        f"  routed AI, local box        ${m['routed_local_box']:>10,.2f}",
        f"  routed AI, cloud-only       ${m['routed_cloud_only']:>10,.2f}",
        "",
        f"  recommended configuration:  {res['recommended_configuration']}"
        f"   (service fee ${m['service_fee']:,.0f}/mo on top)",
        (f"  local box pays for itself from ~{be:,} tasks/month"
         if be else "  local box break-even: n/a at these rates"),
        "",
        "=== What the client keeps (net of fees) ===",
        f"  vs people-only              ${s['vs_human_baseline']:>10,.2f} / month",
        f"  vs naive AI                 ${s['vs_naive_ai']:>10,.2f} / month",
        f"  payback on setup fee        {res['payback_months_on_setup_fee'] or 'n/a'} months",
        "",
        "=== Sensitivity (volume) ===",
    ]
    for row in res["sensitivity"]:
        lines.append(f"  {row['tasks_per_month']:>7,} tasks/mo [{row['recommended']:<9}] "
                     f"net vs human ${row['net_vs_human']:>10,.2f}   vs naive ${row['net_vs_naive']:>10,.2f}")
    lines.append("")
    lines.append("=== Read the fine print ===")
    for note in res["honesty"]:
        lines.append(f"  - {note}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    d = Params()
    p.add_argument("--tasks-per-month", type=int, default=d.tasks_per_month)
    p.add_argument("--minutes-per-task", type=float, default=d.minutes_per_task_human)
    p.add_argument("--hourly-usd", type=float, default=d.loaded_hourly_usd)
    p.add_argument("--tokens-in", type=int, default=d.tokens_in_per_call,
                   help="input tokens PER MODEL CALL")
    p.add_argument("--tokens-out", type=int, default=d.tokens_out_per_call,
                   help="output tokens PER MODEL CALL")
    p.add_argument("--calls-per-task", type=int, default=d.calls_per_task)
    p.add_argument("--automatable", type=float, default=d.automatable_fraction)
    p.add_argument("--local-success", type=float, default=None,
                   help="override the local success rate (default: measured, else 0.85)")
    p.add_argument("--infra-month", type=float, default=d.local_infra_usd_month)
    p.add_argument("--setup-fee", type=float, default=d.setup_fee_usd)
    p.add_argument("--monthly-fee", type=float, default=d.service_fee_usd_month)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    a = p.parse_args(argv)

    kwargs = dict(
        tasks_per_month=a.tasks_per_month,
        minutes_per_task_human=a.minutes_per_task,
        loaded_hourly_usd=a.hourly_usd,
        tokens_in_per_call=a.tokens_in,
        tokens_out_per_call=a.tokens_out,
        calls_per_task=a.calls_per_task,
        automatable_fraction=a.automatable,
        local_infra_usd_month=a.infra_month,
        setup_fee_usd=a.setup_fee,
        service_fee_usd_month=a.monthly_fee,
    )
    if a.local_success is not None:  # explicit override beats the measured default
        kwargs["local_success_rate"] = a.local_success
    res = compute(Params(**kwargs), use_measured=(a.local_success is None))
    print(json.dumps(res, indent=2) if a.json else _fmt_table(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
