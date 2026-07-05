"""Measure ModelRouter.classify() accuracy against a labeled task->tier set."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import ModelRouter  # noqa: E402

TIERS = ("CRITICAL", "COMPLEX", "MODERATE", "SIMPLE")


def load_dataset(path) -> list:
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def evaluate(dataset: list, router: ModelRouter | None = None, classify=None) -> dict:
    """`classify`: optional `task -> tier` strategy to score (default: the keyword
    classifier, router.classify). Pass e.g. `lambda t: router.classify_via_model(t)[0]`
    for 9B-primary or `lambda t: router._classify(t)[0]` for the keyword-first+9B
    hybrid, to compare strategies on the same labeled set."""
    router = router or ModelRouter(log_decisions=False)
    classify = classify or router.classify
    confusion: dict = {a: defaultdict(int) for a in TIERS}
    correct = 0
    support: dict = defaultdict(int)
    pred_count: dict = defaultdict(int)
    tp: dict = defaultdict(int)
    for row in dataset:
        actual = row["expected_tier"]
        predicted = classify(row["task"])
        confusion[actual][predicted] += 1
        support[actual] += 1
        pred_count[predicted] += 1
        if predicted == actual:
            correct += 1
            tp[actual] += 1
    n = len(dataset)
    per_tier = {}
    for t in TIERS:
        prec = tp[t] / pred_count[t] if pred_count[t] else 0.0
        rec = tp[t] / support[t] if support[t] else 0.0
        per_tier[t] = {"precision": round(prec, 3), "recall": round(rec, 3), "support": support[t]}
    return {
        "n": n,
        "accuracy": round(correct / n, 3) if n else 0.0,
        "per_tier": per_tier,
        "confusion": {a: dict(d) for a, d in confusion.items()},
    }


def main() -> int:
    data = load_dataset(Path(__file__).parent / "datasets" / "labeled_tasks.jsonl")
    print(json.dumps(evaluate(data), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
