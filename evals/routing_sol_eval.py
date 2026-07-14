"""Routing-SOL: a speed-of-light (oracle) replay for routing decisions.

Borrowed from the SOL methodology in NVIDIA's Nemotron-Labs-Diffusion tech
report: before optimizing a component, compute an achievable-by-definition
upper bound and report the gap to it. Here the component is the ROUTER and the
bound is hindsight-optimal model assignment over outcome-labeled sessions:

  - success + confidently-classified tier -> the oracle pays that tier's
    chain-lead list rate over the session's tokens (local lead = $0). This is
    the OVER-ROUTING pool: frontier spend a confident cheap signal covers.
  - success + defaulted/unconfident tier  -> oracle = actual. NO CLAIM: savings
    we cannot attribute to a confident routing signal are not counted.
  - failure that leaned on a cheaper model -> hindsight-optimal is escalating
    immediately, so the oracle PAYS the frontier reprice (ESCALATION_MODEL) of
    the session's tokens. This is the UNDER-ROUTING penalty.
  - failure entirely on the frontier      -> oracle = actual. No better move
    existed in hindsight.
  - unlabeled sessions are excluded from the bound and reported separately.

`headroom_usd = actual - oracle` over labeled sessions. Like the paper's SOL,
this is a CEILING on what better routing could save — an assumption-laden
bound, not a promise. Its core assumption (a confident cheap-tier success
would still have succeeded on the cheap tier) is the same one
routing_quality_eval's `strong_downgrade_candidates` makes, here aggregated
into a single number with an over/under decomposition.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import ModelRouter  # noqa: E402
from telemetry import pricing  # noqa: E402
from evals.routing_quality_eval import tier_of  # noqa: E402

# The model the oracle escalates failures to. Matches the frontier model real
# sessions ran on, so the reprice is comparable to observed imputed_usd.
ESCALATION_MODEL = "claude-opus-4-8"

ASSUMPTIONS = [
    "a confidently-classified success would still succeed on its tier's chain-lead model",
    f"a failure that used a non-frontier model is repriced as one {ESCALATION_MODEL} run over the same tokens",
    "unconfident (defaulted) classifications claim no savings",
    "unlabeled sessions are excluded from the bound entirely",
]

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")


def _aggregate_sessions(events: list) -> dict:
    """session_id -> {task, usd, outcome, tokens{field:n}, non_frontier, events}."""
    sessions: dict = {}
    for e in events:
        if e.get("event") != "usage" or not e.get("task_text"):
            continue
        key = e.get("session_id") or f"_noid:{e.get('msg_id') or id(e)}"
        s = sessions.get(key)
        if s is None:
            s = sessions[key] = {
                "task": e["task_text"], "usd": 0.0, "outcome": None,
                "tokens": dict.fromkeys(_TOKEN_FIELDS, 0),
                "non_frontier": False, "events": 0,
            }
        s["usd"] += float(e.get("imputed_usd") or 0.0)
        s["events"] += 1
        for f in _TOKEN_FIELDS:
            s["tokens"][f] += int(e.get(f) or 0)
        if tier_of(e.get("model")) in ("mid", "local"):
            s["non_frontier"] = True
        if s["outcome"] is None and e.get("outcome"):
            s["outcome"] = e.get("outcome")
    return sessions


def evaluate(events: list, router: ModelRouter | None = None) -> dict:
    router = router or ModelRouter(log_decisions=False)
    sessions = _aggregate_sessions(events)

    actual = oracle = over = under = no_claim = 0.0
    n_labeled = n_unlabeled = 0
    per_tier: dict = defaultdict(lambda: {"sessions": 0, "actual_usd": 0.0, "oracle_usd": 0.0})
    over_routed_rows = []

    for sid, s in sessions.items():
        if s["outcome"] not in ("success", "failure"):
            n_unlabeled += 1
            continue
        n_labeled += 1
        actual += s["usd"]
        # _classify = the live-routing path: keyword-only by default; with
        # use_model_classifier=True it is the keyword-first + 9B-rescue hybrid,
        # and a model-rescued tier counts as confident (same as live routing).
        tier, confident = router._classify(s["task"])

        if s["outcome"] == "success":
            if confident:
                lead = router.preferences.get(tier, [None])[0]
                o = pricing.imputed_usd(lead, **s["tokens"]) if lead else s["usd"]
                over += max(s["usd"] - o, 0.0)
                if s["usd"] > o:
                    over_routed_rows.append({
                        "session_id": sid[:12], "tier": tier,
                        "actual_usd": round(s["usd"], 4), "oracle_usd": round(o, 4),
                        "task": (s["task"] or "")[:80],
                    })
            else:
                o = s["usd"]
                no_claim += s["usd"]
        else:  # failure
            if s["non_frontier"]:
                o = pricing.imputed_usd(ESCALATION_MODEL, **s["tokens"])
                under += o
            else:
                o = s["usd"]
        oracle += o

        t = per_tier[tier]
        t["sessions"] += 1
        t["actual_usd"] = round(t["actual_usd"] + s["usd"], 4)
        t["oracle_usd"] = round(t["oracle_usd"] + o, 4)

    headroom = actual - oracle
    over_routed_rows.sort(key=lambda r: r["actual_usd"] - r["oracle_usd"], reverse=True)
    return {
        "n_sessions": len(sessions),
        "n_labeled": n_labeled,
        "n_unlabeled": n_unlabeled,
        "actual_usd": round(actual, 4),
        "oracle_usd": round(oracle, 4),
        "headroom_usd": round(headroom, 4),
        "headroom_pct": round(headroom / actual * 100, 1) if actual else 0.0,
        "over_routing_usd": round(over, 4),
        "under_routing_usd": round(under, 4),
        "no_claim_usd": round(no_claim, 4),
        "per_tier": {t: dict(v) for t, v in per_tier.items()},
        "top_over_routed": over_routed_rows[:10],
        "assumptions": ASSUMPTIONS,
    }


def main() -> int:
    from telemetry import schema
    print(json.dumps(evaluate(schema.read_events()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
