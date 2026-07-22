"""The promotion gate: does the tuned SLM replace the incumbent router?

This is the decision S5/S6 built toward and never got to make. The rule is the
project's standing one, encoded so it cannot be fudged after seeing the numbers:

    PROMOTE iff  challenger_accuracy >= incumbent_accuracy
            AND  no tier's recall regresses by more than TIER_TOLERANCE

Why a tolerance rather than zero: per-tier recall on ~30 rows moves in ~3-point
steps, so a literal zero-regression rule rejects on noise. The tolerance is
declared up front, not tuned to the result.

Three configurations are measured on the SAME rows:

  incumbent      classify_hybrid — keyword-first, 9B rescue when keywords default.
                 This is what actually runs in production; the 9B alone is NOT the
                 incumbent, and measuring against it would flatter the challenger.
  e2b_standalone the tuned E2B answering every task by itself.
  e2b_rescue     keyword-first with the tuned E2B as the rescue model — the drop-in
                 swap that tied the incumbent at 0.810 on the 42-row union.

The 9B must be SERVED (localhost:8080) and reached through the production
ModelClassifier. Driving it through the MLX harness instead yields the
always-MODERATE floor (it is a reasoning model whose preamble overruns the
8-token budget) — an artifact this project has now been bitten by three times.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llmops  # noqa: E402
from evals.router_classification_eval import evaluate  # noqa: E402

TIER_TOLERANCE = 0.05  # declared before the run; see module docstring


def load_rows(path: Path) -> list:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                d = json.loads(line)
                if d.get("task") and d.get("expected_tier"):
                    rows.append(d)
    return rows


def per_tier_recall(result: dict) -> dict:
    return {t: round(v["recall"], 4) for t, v in (result.get("per_tier") or {}).items()}


def decide(incumbent: dict, challenger: dict, tolerance: float = TIER_TOLERANCE) -> dict:
    """Apply the promotion rule. Returns the verdict plus every reason, so a
    rejection names which tier failed rather than just saying no."""
    inc_acc, chal_acc = incumbent["accuracy"], challenger["accuracy"]
    inc_t, chal_t = per_tier_recall(incumbent), per_tier_recall(challenger)
    regressions = {}
    for tier, inc_r in inc_t.items():
        delta = chal_t.get(tier, 0.0) - inc_r
        if delta < -tolerance:
            regressions[tier] = round(delta, 4)
    accuracy_ok = chal_acc >= inc_acc
    return {
        "promote": bool(accuracy_ok and not regressions),
        "accuracy_ok": accuracy_ok,
        "accuracy_delta": round(chal_acc - inc_acc, 4),
        "tier_regressions": regressions,
        "tolerance": tolerance,
    }


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Router promotion gate")
    p.add_argument("--dataset", default=str(repo / "evals/datasets/labeled_tasks_github.jsonl"))
    p.add_argument("--e2b-base", default="/Volumes/1TB NVMe/models/mlx-community/gemma-4-e2b-it-4bit")
    p.add_argument("--e2b-adapter", default=str(repo / "evals/adapters/e2b_v2"))
    p.add_argument("--out", default=str(repo / "logs/promotion_gate.json"))
    a = p.parse_args(argv)

    rows = load_rows(Path(a.dataset))
    tiers = collections.Counter(r["expected_tier"] for r in rows)
    print(f"dataset: {Path(a.dataset).name}  n={len(rows)}  tiers={dict(tiers)}", file=sys.stderr)

    results = {}

    # --- incumbent: keyword-first + 9B rescue (what production runs) --------
    router = llmops.ModelRouter(use_model_classifier=True, log_decisions=False)
    print("[1/3] incumbent classify_hybrid (keyword + 9B rescue)...", file=sys.stderr)
    results["incumbent_hybrid_9b"] = evaluate(rows, classify=lambda t: router.classify_hybrid(t)[0])

    # --- challengers: the tuned SLM, standalone and as the rescue model -----
    from evals.classify_finetuned import make_classifier
    e2b = make_classifier(a.e2b_base, a.e2b_adapter)

    print("[2/3] challenger e2b_standalone...", file=sys.stderr)
    results["e2b_standalone"] = evaluate(rows, classify=e2b)

    print("[3/3] challenger e2b_rescue (keyword-first + tuned E2B)...", file=sys.stderr)
    kw_router = llmops.ModelRouter(use_model_classifier=False, log_decisions=False)
    counts = collections.Counter()

    def hybrid_e2b(task: str) -> str:
        tier, matched = kw_router.classify_detailed(task)
        if matched:
            counts["keyword"] += 1
            return tier
        counts["e2b_rescue"] += 1
        return e2b(task)

    results["e2b_rescue"] = evaluate(rows, classify=hybrid_e2b)

    inc = results["incumbent_hybrid_9b"]
    report = {
        "dataset": Path(a.dataset).name,
        "n": len(rows),
        "tiers": dict(tiers),
        "tolerance": TIER_TOLERANCE,
        "rescue_path_counts": dict(counts),
        "accuracy": {k: round(v["accuracy"], 4) for k, v in results.items()},
        "per_tier_recall": {k: per_tier_recall(v) for k, v in results.items()},
        "verdicts": {name: decide(inc, results[name])
                     for name in ("e2b_standalone", "e2b_rescue")},
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({**report, "raw": results}, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
