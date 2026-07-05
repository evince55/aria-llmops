"""Outcome-aware routing-quality eval — the other half of the routing loop.

`routing_efficiency_eval` is cost-only: it asks what tier the router *would*
pick, never whether the work actually succeeded. This eval joins the per-session
OUTCOME signal (telemetry/outcomes.py) onto spend and model mix to answer the two
questions that close the loop between "route cheap" and "route cheap without
losing quality":

  1. Did cheap routing HURT?  ->  `cheap_routing_failures`: labeled *failure*
     sessions that leaned on a non-frontier (mid/local) model. These are the
     regressions we can actually attribute to routing down. If this list is
     empty, no observed failure is attributable to a cheaper model — a real,
     reportable result, not an absence of data.

  2. Where is frontier spend UNJUSTIFIED?  ->  `downgrade_candidates`: expensive
     *success* sessions that ran entirely on the frontier model, ranked by spend.
     They already succeeded, so trying a cheaper tier there is the highest-upside,
     lowest-risk routing change available.

Cost is real; outcome is a high-precision heuristic/model label, so unlabeled
sessions are reported separately and NEVER assumed good or bad.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry import schema  # noqa: E402


def tier_of(model: str) -> str:
    """Map a model id onto a routing tier. Frontier = the expensive default we're
    trying to route *away* from; mid/local = the cheaper targets."""
    m = (model or "").lower()
    if "opus" in m:
        return "frontier"
    if "sonnet" in m:
        return "mid"
    if any(t in m for t in ("llama", "qwen", "mythos", "local")):
        return "local"
    return "other"


def _aggregate_sessions(events: list) -> dict:
    """session_id -> {usd, events, outcome, tier_events{tier:n}, tier_usd{tier:$}}."""
    sessions: dict = {}
    for e in events:
        if e.get("event") != "usage":
            continue
        key = e.get("session_id") or f"_noid:{e.get('msg_id') or id(e)}"
        s = sessions.get(key)
        if s is None:
            s = sessions[key] = {
                "usd": 0.0, "events": 0, "outcome": None,
                "tier_events": defaultdict(int), "tier_usd": defaultdict(float),
                "task": e.get("task_text"),
            }
        usd = float(e.get("imputed_usd") or 0.0)
        tier = tier_of(e.get("model"))
        s["usd"] += usd
        s["events"] += 1
        s["tier_events"][tier] += 1
        s["tier_usd"][tier] += usd
        # outcome is stamped identically on every event in a labeled session;
        # keep the first non-null we see.
        if s["outcome"] is None and e.get("outcome"):
            s["outcome"] = e.get("outcome")
    return sessions


def evaluate(events: list, min_candidate_usd: float = 5.0, top_n: int = 10) -> dict:
    sessions = _aggregate_sessions(events)

    outcomes: dict = defaultdict(int)
    spend_by_outcome: dict = defaultdict(lambda: {"sessions": 0, "usd": 0.0})
    cheap_routing_failures = []
    downgrade_candidates = []
    addressable_usd = 0.0  # frontier-only success spend a cheaper tier could target

    for sid, s in sessions.items():
        label = s["outcome"] or "unlabeled"
        outcomes[label] += 1
        b = spend_by_outcome[label]
        b["sessions"] += 1
        b["usd"] = round(b["usd"] + s["usd"], 4)

        non_frontier = {t: n for t, n in s["tier_events"].items() if t not in ("frontier", "other")}
        frontier_only = not non_frontier and s["tier_events"].get("frontier", 0) > 0

        if s["outcome"] == "failure" and non_frontier:
            # A failure that leaned on a cheaper model -> routing-down suspect.
            cheap_routing_failures.append({
                "session_id": sid[:12], "usd": round(s["usd"], 4),
                "non_frontier_tiers": {t: n for t, n in non_frontier.items()},
                "task": (s["task"] or "")[:80],
            })

        if s["outcome"] == "success" and frontier_only:
            addressable_usd += s["usd"]
            if s["usd"] >= min_candidate_usd:
                downgrade_candidates.append({
                    "session_id": sid[:12], "usd": round(s["usd"], 4),
                    "events": s["events"], "task": (s["task"] or "")[:80],
                })

    downgrade_candidates.sort(key=lambda r: r["usd"], reverse=True)
    n_labeled = outcomes.get("success", 0) + outcomes.get("failure", 0)
    succ = spend_by_outcome.get("success", {"sessions": 0, "usd": 0.0})

    return {
        "n_sessions": len(sessions),
        "n_labeled": n_labeled,
        "outcomes": dict(outcomes),
        "spend_by_outcome": {k: dict(v) for k, v in spend_by_outcome.items()},
        "usd_per_successful_session": round(succ["usd"] / succ["sessions"], 4) if succ["sessions"] else 0.0,
        # Q1: cheap routing that coincided with a failed outcome.
        "cheap_routing_failures": cheap_routing_failures,
        # Q2: frontier-only successes worth retrying on a cheaper tier.
        "downgrade_candidates": downgrade_candidates[:top_n],
        # Total $ locked in frontier-only successes — the pool a cheaper-routing
        # experiment could address (upper bound on savings, not a promise).
        "addressable_frontier_success_usd": round(addressable_usd, 4),
    }


def main() -> int:
    print(json.dumps(evaluate(schema.read_events()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
