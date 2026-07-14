"""Compare classification strategies — keyword vs 9B-primary vs keyword-first+9B
hybrid — across the keyword-tuned seed set, the novel-prose set, and their union.

The two datasets measure different regimes:
  - labeled_tasks.jsonl        : keyword-aligned; keyword ~100% (overfit). Contains
    the rows the 9B UNDER-rates (AVAudioEngine, race), so it's where the hybrid's
    keyword-first behavior earns its keep.
  - labeled_tasks_prose.jsonl  : every row is keyword-BLIND (keyword defaults to
    MODERATE). Isolates the 9B's prose generalization; keyword is near-useless here.

Union is the honest overall instrument. Expectation: hybrid >= max(keyword, 9B).
Caveat: prose-set labels are authored/curated (see `source` field), so treat the
prose column as directional, not ground truth. The hybrid-vs-9B claim rests on the
keyword-tuned rows, whose labels are trustworthy.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import ModelRouter  # noqa: E402
from evals.router_classification_eval import load_dataset, evaluate  # noqa: E402

_DS = Path(__file__).parent / "datasets"


def _strategies(router: ModelRouter):
    return {
        # The no-router floor: what accuracy costs if we always shrug. Every
        # other row's value is its lift over this baseline (ablation-table style).
        "default-MODERATE": lambda t: "MODERATE",
        "keyword":    router.classify,
        "9B-primary": lambda t: router.classify_via_model(t)[0],
        "hybrid":     lambda t: router.classify_hybrid(t)[0],
    }


def compare(router: ModelRouter | None = None) -> dict:
    router = router or ModelRouter(log_decisions=False, use_model_classifier=True)
    seed = load_dataset(_DS / "labeled_tasks.jsonl")
    prose = load_dataset(_DS / "labeled_tasks_prose.jsonl")
    datasets = {"keyword_tuned": seed, "prose_blind": prose, "union": seed + prose}
    out: dict = {}
    for strat_name, classify in _strategies(router).items():
        out[strat_name] = {
            ds_name: evaluate(rows, router=router, classify=classify)["accuracy"]
            for ds_name, rows in datasets.items()
        }
    out["_n"] = {k: len(v) for k, v in datasets.items()}
    return out


def main() -> int:
    res = compare()
    n = res.pop("_n")
    print(f"datasets: keyword_tuned={n['keyword_tuned']}  prose_blind={n['prose_blind']}  union={n['union']}\n")
    print(f"{'strategy':<12} {'keyword_tuned':>14} {'prose_blind':>12} {'union':>8}")
    for strat, accs in res.items():
        print(f"{strat:<12} {accs['keyword_tuned']:>14.3f} {accs['prose_blind']:>12.3f} {accs['union']:>8.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
