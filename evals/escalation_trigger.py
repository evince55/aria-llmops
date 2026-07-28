"""Does classifier agreement predict correctness? The router's escalation trigger.

Rule A — agreement means trust — is the only round-2 claim that survived every
instrument change: the parser fix, the wide set, the operating points, the
template mode. Every accuracy number in that round moved at least once; this one
did not. So it is the one worth trying to deploy.

WHY NOT THE TOOL ADAPTER. Nothing consumes tool calls: `llmops.py`,
`mlx_classifier.py`, telemetry and the dashboard contain zero references to them,
and the `LLMOPS_MLX_ADAPTER` seam serves round 1's *classifier*. The tool adapter
was chosen for *measurement headroom*, which is exactly what makes it a poor
deployment candidate — it solves a surface I invented, for no caller.

WHAT IS DIFFERENT HERE, AND WHY IT IS A STRONGER TEST. Round 2 measured Rule A on
a synthetic tool-call set whose ground truth I authored. This measures it on 176
GitHub issues with 3-lab unanimous human labels, using the two arms that are
already deployed. A finding that transfers from a set I wrote to a set I did not
is worth considerably more than one that does not.

The gate is pre-registered in
`docs/research/2026-07-27-escalation-trigger-preregistration.md` and applied here
rather than described: BOTH `precision_on_covered >= 0.90` and `coverage >= 0.50`,
or it does not ship.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.ensemble import agree_or_escalate, cascade_score  # noqa: E402

MIN_PRECISION = 0.90
MIN_COVERAGE = 0.50


def gate(summary: dict, min_precision=MIN_PRECISION, min_coverage=MIN_COVERAGE) -> dict:
    """The deployment decision, computed rather than eyeballed.

    Two criteria, both required, for reasons that pull in opposite directions:
    covered rows ship WITHOUT escalation so they must be safe, and escalation
    costs money so a trigger that escalates most traffic is a tax with extra
    steps. Optimising either alone is trivial and useless — abstain on everything
    for perfect precision, or accept everything for perfect coverage.
    """
    p = summary.get("precision_on_covered")
    c = summary.get("coverage", 0.0)
    safe = p is not None and p >= min_precision
    useful = c >= min_coverage
    return {"precision_on_covered": p, "coverage": round(c, 4),
            "min_precision": min_precision, "min_coverage": min_coverage,
            "safe_enough": safe, "covers_enough": useful,
            "ship": bool(safe and useful)}


def load_tasks(path: str) -> list:
    rows = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            d = json.loads(line)
            rows.append({"task": d["task"], "truth": d["expected_tier"]})
    return rows


def run(tasks, keyword_fn, model_fn) -> dict:
    """Score Rule A over two classifier arms.

    Both arms answer every row; disagreement escalates. The per-row detail is
    kept because an aggregate that looks good can still be concentrated in one
    tier, and the router cares which tier it is wrong about.
    """
    rows, detail = [], []
    for t in tasks:
        a, b = keyword_fn(t["task"]), model_fn(t["task"])
        r = agree_or_escalate([a, b])
        r["truth"] = t["truth"]
        rows.append(r)
        detail.append({"task": t["task"][:120], "truth": t["truth"],
                       "keyword": a, "model": b, "accepted": r["accepted"]})
    summary = cascade_score(rows)
    # Where a trigger is wrong matters as much as how often: shipping a wrong
    # CRITICAL is not the same cost as shipping a wrong SIMPLE.
    by_tier = {}
    for r, d in zip(rows, detail):
        if not r["accepted"]:
            continue
        tier = r["truth"]
        ok, n = by_tier.get(tier, (0, 0))
        by_tier[tier] = (ok + (r["call"] == tier), n + 1)
    summary["covered_precision_by_tier"] = {
        k: {"correct": v[0], "n": v[1], "precision": round(v[0] / v[1], 4)}
        for k, v in sorted(by_tier.items())}
    return {"summary": summary, "gate": gate(summary), "detail": detail}


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Router escalation trigger: Rule A on real tasks")
    p.add_argument("--tasks", default=str(repo / "evals/datasets/labeled_tasks_github.jsonl"))
    p.add_argument("--out", default=str(repo / "evals/results/escalation_trigger.json"))
    a = p.parse_args(argv)

    sys.path.insert(0, str(repo))
    from llmops import ModelRouter  # noqa: E402

    router = ModelRouter()
    if getattr(router, "classifier_client", None) is None:
        raise SystemExit("no model classifier available; this measures the DEPLOYED pair "
                         "and a keyword-only run would not be the trigger under test")

    def keyword_fn(task):
        tier, matched = router.classify_detailed(task)
        # An unmatched keyword default is the classifier saying "I have no
        # signal". Treating it as a confident MODERATE would let the trigger
        # ship rows on the strength of a fallback.
        return tier if matched else None

    def model_fn(task):
        # classify_via_model returns (tier, source) where source is "model" or
        # "keyword-fallback". A fallback is NOT a second opinion — it is the
        # keyword arm again, and counting it as agreement would measure one arm
        # against itself, which is the correlation confound this round already
        # flagged in its pre-registration.
        #
        # Nothing is caught here on purpose. The first version wrapped this in
        # `except Exception: return None`, an AttributeError from a guessed API
        # became 176 silent abstentions, and coverage 0.0 was printed as a
        # result. A harness must let its own bugs crash it.
        tier, source = router.classify_via_model(task)
        return tier if source == "model" else None

    res = run(load_tasks(a.tasks), keyword_fn, model_fn)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2))
    print(json.dumps({"summary": res["summary"], "gate": res["gate"]}, indent=2))
    print(f"\n  SHIP: {res['gate']['ship']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
