"""Cost-per-task control — the project's second pillar, measured.

WHAT THE DATA SAID, before any of this was written. Of 218 recorded route
decisions, **213 (97.7%) chose the free local model**, so `estimated_usd` was
0.0 by construction; the assumed output ratio in `CostMonitor.estimate_cost` was
being multiplied by zero, and `should_route_to_local()` had never had anything to
gate. Only **3 of 218** decisions could be joined to an actual usage record. The
pillar was not merely unvalidated — it was unexercised.

That changes what "control" should mean here. For a local-first router the
interesting number is not what it spent (~$0) but **what local-first bought**:
the counterfactual cost of the same measured work on a priced model. Nothing
computed that before this module.

TWO HONESTY RULES, both easy to violate in a way that flatters the result:

1. **Coverage travels with the error.** `estimate_error` reports how many
   decisions it could actually join, and refuses to imply a conclusion from a
   handful of rows (`sufficient`). A mean absolute error over 3 of 218 decisions
   is not a measurement of the cost model.
2. **The counterfactual prices MEASURED tokens.** It never applies an assumed
   input/output split — that is the unvalidated 0.4 this pillar exists to stop
   relying on (see A1, `llmops.DEFAULT_OUTPUT_RATIO`). An assumption multiplied
   by a made-up ratio is not a measurement.

Stdlib only; consumes the ledger written by `telemetry.schema`.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

# Below this many joined decisions, an estimate-vs-actual error is reported but
# explicitly marked insufficient. Chosen so a handful of rows cannot look like a
# verdict; it is a floor on honesty, not a statistical threshold.
MIN_JOINED_FOR_CONCLUSION = 20


def _f(row, key) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _i(row, key) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _usage(events):
    return [e for e in (events or []) if e.get("event") == "usage"]


def _key(text) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()[:120]


def router_spend(events) -> dict:
    """Actual and imputed spend, overall and per model."""
    rows = _usage(events)
    by = defaultdict(lambda: {"imputed_usd": 0.0, "actual_usd": 0.0, "n": 0})
    for r in rows:
        b = by[r.get("model", "unknown")]
        b["imputed_usd"] += _f(r, "imputed_usd")
        b["actual_usd"] += _f(r, "actual_usd")
        b["n"] += 1
    return {"by_model": {k: dict(v) for k, v in by.items()},
            "total_imputed_usd": sum(v["imputed_usd"] for v in by.values()),
            "total_actual_usd": sum(v["actual_usd"] for v in by.values()),
            "n_events": len(rows)}


def counterfactual(events, model: str, rates=None) -> dict:
    """What this workload WOULD have cost on `model`, priced on measured tokens.

    Deliberately not an estimate: it multiplies the input/output counts actually
    recorded by that model's published rates. No assumed split is involved.
    """
    if rates is None:
        import llmops
        rates = llmops.MODEL_RATES
    rate = rates.get(model)
    if rate is None:
        raise SystemExit(f"counterfactual: no rate for {model!r} — add it to MODEL_RATES "
                         f"or pass rates=")
    rows = _usage(events)
    tin = sum(_i(r, "input_tokens") for r in rows)
    tout = sum(_i(r, "output_tokens") for r in rows)
    usd = tin / 1_000_000 * rate["input"] + tout / 1_000_000 * rate["output"]
    return {"model": model, "usd": usd, "input_tokens": tin, "output_tokens": tout,
            "n_tasks": len(rows), "usd_per_task": (usd / len(rows)) if rows else 0.0}


def savings_report(events, cloud_models=None, rates=None, harnesses=None) -> dict:
    """What the local-first policy bought, per priced alternative.

    `harnesses` SCOPES the question to work the router actually executed
    (`harness="llmops-local"`). Without it the ledger's ingested Claude Code
    sessions dominate — they cost far more than the same tokens would on any
    router model, so an unscoped report showed $0.00 saved and hid the result
    entirely. Scope, or the number is meaningless.
    """
    if rates is None:
        import llmops
        rates = llmops.MODEL_RATES
    if cloud_models is None:
        cloud_models = tuple(m for m, r in rates.items() if r["input"] > 0)
    if harnesses is not None:
        events = [e for e in _usage(events) if e.get("harness") in set(harnesses)]
    spend = router_spend(events)
    cf = {m: counterfactual(events, m, rates=rates) for m in cloud_models}
    return {
        "actual_imputed_usd": spend["total_imputed_usd"],
        "actual_usd": spend["total_actual_usd"],
        "n_tasks": spend["n_events"],
        "counterfactual": cf,
        # What was avoided. Never negative: if the work already ran on that
        # model the counterfactual equals what was paid.
        "saved_usd_vs": {m: max(c["usd"] - spend["total_imputed_usd"], 0.0)
                         for m, c in cf.items()},
    }


def estimate_error(decisions, usage_events) -> dict:
    """Was the router's cost ESTIMATE right? Reported with its coverage.

    Joins `route_decision.estimated_usd` to the `usage.imputed_usd` of the same
    task text. Coverage is part of the result because on this repo's ledger only
    3 of 218 decisions join at all — an error computed over those would look
    authoritative and mean nothing.
    """
    dec = [d for d in (decisions or []) if d.get("event") == "route_decision"] or list(decisions or [])
    actual = defaultdict(float)
    for u in _usage(usage_events):
        k = _key(u.get("task_text"))
        if k:
            actual[k] += _f(u, "imputed_usd")
    errs = []
    for d in dec:
        k = _key(d.get("task_text"))
        if k in actual:
            errs.append(abs(_f(d, "estimated_usd") - actual[k]))
    n = len(errs)
    return {
        "n_decisions": len(dec),
        "n_joined": n,
        "coverage": (n / len(dec)) if dec else 0.0,
        "mean_abs_error_usd": (sum(errs) / n) if n else None,
        "max_abs_error_usd": max(errs) if n else None,
        "sufficient": n >= MIN_JOINED_FOR_CONCLUSION,
        "min_joined_for_conclusion": MIN_JOINED_FOR_CONCLUSION,
    }


# Rough chars-per-token. Deliberately crude: these are ESTIMATES from string
# lengths because `opencode run` returns no usage block, and a crude number that
# exists beats an exact one that does not. Everything derived from it is labelled
# "estimated-from-chars" so it can never be mistaken for a billed figure.
CHARS_PER_TOKEN = 4.0
JUDGE_HARNESS = "llmops-judge"


def judge_event(model: str, prompt: str, reply: str, rates=None) -> dict:
    """A usage event for one judge/grader call.

    THE COST CENTRE NOBODY COULD SEE. The router routes to free local models, so
    routed spend is ~$0 — while the eval loop ran the opencode-go subscription to
    100% of its rolling limit in a single day (2026-07-25, ~$7, almost entirely
    A/B grading). `evals.judge_labels.call_judge` shells out to `opencode run`
    and recorded nothing, so the only cost that mattered was invisible.
    """
    if rates is None:
        import llmops
        rates = llmops.MODEL_RATES
    tin = int(len(prompt or "") / CHARS_PER_TOKEN)
    tout = int(len(reply or "") / CHARS_PER_TOKEN)
    rate = rates.get(model)
    # A model with no published rate is FLAGGED, never silently priced at zero.
    # The two graders that exhausted the subscription (opencode-go/glm-5.2 and
    # deepseek-v4-pro) are absent from MODEL_RATES, so a naive lookup reports the
    # most expensive activity in the project as $0.00 — a silent zero on the
    # spend that actually hurts is the worst failure this module could have.
    unpriced = rate is None
    if unpriced:
        rate = {"input": 0.0, "output": 0.0}
    usd = tin / 1_000_000 * rate["input"] + tout / 1_000_000 * rate["output"]
    return {"event": "usage", "harness": JUDGE_HARNESS, "model": model,
            "input_tokens": tin, "output_tokens": tout,
            "imputed_usd": usd, "actual_usd": 0.0,
            "unpriced": unpriced,
            "cost_model": "estimated-from-chars"}


def eval_spend(events) -> dict:
    """Judge/grader spend, separated from routed spend.

    Kept apart on purpose: aggregating them hides that ~100% of this project's
    real spend is evaluation, not inference.
    """
    rows = [e for e in _usage(events) if e.get("harness") == JUDGE_HARNESS]
    by = defaultdict(lambda: {"n": 0, "imputed_usd": 0.0,
                              "input_tokens": 0, "output_tokens": 0})
    for r in rows:
        b = by[r.get("model", "unknown")]
        b["n"] += 1
        b["imputed_usd"] += _f(r, "imputed_usd")
        b["input_tokens"] += _i(r, "input_tokens")
        b["output_tokens"] += _i(r, "output_tokens")
    unpriced = [r for r in rows if r.get("unpriced")]
    return {"n_judge_calls": len(rows),
            "imputed_usd": sum(v["imputed_usd"] for v in by.values()),
            "by_model": {k: dict(v) for k, v in by.items()},
            "n_unpriced_calls": len(unpriced),
            "unpriced_models": sorted({r.get("model") for r in unpriced}),
            "note": ("estimated from character counts; opencode run reports no usage block. "
                     "UNPRICED calls are counted separately — their models have no entry in "
                     "MODEL_RATES, so their cost is NOT included in imputed_usd and the total "
                     "is a FLOOR, not the bill.")}


def main(argv=None) -> int:
    import argparse
    import json
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from telemetry import schema

    p = argparse.ArgumentParser(description="Cost-per-task control report")
    p.add_argument("--ledger", default=None)
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)

    events = schema.read_events(ledger=Path(a.ledger)) if a.ledger else schema.read_events()
    decisions = [e for e in events if e.get("event") == "route_decision"]
    report = {
        "spend": router_spend(events),
        "savings": savings_report(events),
        "estimate_error": estimate_error(decisions, events),
        "eval_spend": eval_spend(events),
        "router_only_savings": savings_report(events, harnesses=("llmops-local",)),
    }
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
