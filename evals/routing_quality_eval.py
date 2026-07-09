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


# Tiers the router considers cheap enough to route away from the frontier. A
# frontier-only success the router ITSELF labels one of these is a strong
# downgrade candidate; COMPLEX/CRITICAL means the frontier model may be warranted.
_CHEAP_TIERS = {"SIMPLE", "MODERATE"}


def _norm_classify(tier_result):
    """Accept either a plain `tier` str or a `(tier, confident)` tuple (e.g.
    ModelRouter.classify_detailed, whose `matched=False` means 'defaulted to
    MODERATE, no rule fired' — NOT a real cheap-tier signal). Returns
    (tier, confident); a plain str is taken at its word (confident=True)."""
    if isinstance(tier_result, tuple):
        tier, confident = tier_result[0], bool(tier_result[1])
    else:
        tier, confident = tier_result, True
    return tier, confident


def evaluate(events: list, min_candidate_usd: float = 5.0, top_n: int = 10,
             classify=None) -> dict:
    """`classify`: optional `task_text -> tier` (or `-> (tier, confident)`, e.g.
    ModelRouter.classify_detailed). When given, downgrade candidates are annotated
    with the router's own tier verdict and a `strong_downgrade_candidates` view is
    added — the sharper "spend the router *confidently* thinks was over-provisioned"
    signal. A low-confidence/defaulted tier does NOT count as strong."""
    sessions = _aggregate_sessions(events)

    outcomes: dict = defaultdict(int)
    spend_by_outcome: dict = defaultdict(lambda: {"sessions": 0, "usd": 0.0})
    cheap_routing_failures = []
    frontier_success = []           # every frontier-only success (pre-threshold)
    addressable_usd = 0.0           # frontier-only success spend a cheaper tier could target

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
            frontier_success.append({
                "session_id": sid[:12], "usd": round(s["usd"], 4),
                "events": s["events"], "task": (s["task"] or "")[:80],
                # Classification must see the FULL task text; the 80-char
                # display cut previously fed the classifier too, hiding any
                # tier signal past char 80 (e.g. a trailing "...credentials in
                # the keychain" never registered CRITICAL, so real frontier
                # work got mislabeled a strong downgrade candidate).
                "_full_task": s["task"] or "",
            })

    # Annotate with the router's own tier verdict, if a classifier was supplied.
    # Classify on the full task text (display stays truncated at 80 chars).
    strong = None
    addressable_strong_usd = None
    if classify is not None:
        for f in frontier_success:
            tier, confident = _norm_classify(classify(f.pop("_full_task")))
            f["router_tier"] = tier
            f["router_confident"] = confident
        # Strong = router CONFIDENTLY places it in a cheap tier. A defaulted
        # MODERATE (confident=False) is the classifier shrugging, not evidence.
        strong = [f for f in frontier_success
                  if f["router_tier"] in _CHEAP_TIERS and f["router_confident"]]
        addressable_strong_usd = round(sum(f["usd"] for f in strong), 4)

    for f in frontier_success:      # internal-only field; never in results
        f.pop("_full_task", None)
    frontier_success.sort(key=lambda r: r["usd"], reverse=True)
    downgrade_candidates = [f for f in frontier_success if f["usd"] >= min_candidate_usd][:top_n]
    n_labeled = outcomes.get("success", 0) + outcomes.get("failure", 0)
    succ = spend_by_outcome.get("success", {"sessions": 0, "usd": 0.0})

    out = {
        "n_sessions": len(sessions),
        "n_labeled": n_labeled,
        "outcomes": dict(outcomes),
        "spend_by_outcome": {k: dict(v) for k, v in spend_by_outcome.items()},
        "usd_per_successful_session": round(succ["usd"] / succ["sessions"], 4) if succ["sessions"] else 0.0,
        # Q1: cheap routing that coincided with a failed outcome.
        "cheap_routing_failures": cheap_routing_failures,
        # Q2: frontier-only successes worth retrying on a cheaper tier.
        "downgrade_candidates": downgrade_candidates,
        # Total $ locked in frontier-only successes — the pool a cheaper-routing
        # experiment could address (upper bound on savings, not a promise).
        "addressable_frontier_success_usd": round(addressable_usd, 4),
    }
    if classify is not None:
        # Sharper Q2: the subset the router itself would route to a cheap tier.
        out["router_classified"] = True
        out["strong_downgrade_candidates"] = sorted(
            strong, key=lambda r: r["usd"], reverse=True)[:top_n]
        out["addressable_strong_usd"] = addressable_strong_usd
    return out


def main() -> int:
    from llmops import ModelRouter  # local import; keeps the eval importable standalone
    router = ModelRouter(log_decisions=False)
    # classify_detailed -> (tier, matched): a defaulted MODERATE (matched=False)
    # is not counted as a confident cheap-tier signal.
    print(json.dumps(evaluate(schema.read_events(), classify=router.classify_detailed), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
