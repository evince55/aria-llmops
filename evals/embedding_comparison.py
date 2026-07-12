"""Compare tier-classification strategies on the honest prose-blind set:
keyword (baseline), 9B-hybrid, and the semantic embedding k-NN classifier.

Two embedding evaluations, to separate "does the signal exist" from "does the
current reference set transfer":
  - LOO   : leave-one-out on the prose set (embed prose once; classify each row
            by k-NN over the OTHER prose rows). Tests embeddings on prose-style
            tasks directly, no style mismatch, no separate reference needed.
  - XREF  : the keyword-tuned set as reference -> prose set as test. Tests whether
            short keyword-y reference examples transfer to real prose.

Needs the local embedding server. Run from repo root: python3 evals/embedding_comparison.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.router_classification_eval import evaluate, load_dataset, TIERS  # noqa: E402
from evals.embedding_classifier import EmbeddingClassifier, embed, _cosine  # noqa: E402
from llmops import ModelRouter  # noqa: E402

DS = Path(__file__).resolve().parent / "datasets"
K = int((__import__("os").environ.get("EMBED_KNN_K", "5")))


def _acc_and_tiers(dataset, predictions):
    correct = sum(1 for row, p in zip(dataset, predictions) if p == row["expected_tier"])
    support, tp, pred = defaultdict(int), defaultdict(int), defaultdict(int)
    for row, p in zip(dataset, predictions):
        support[row["expected_tier"]] += 1
        pred[p] += 1
        if p == row["expected_tier"]:
            tp[row["expected_tier"]] += 1
    per_tier = {t: {"precision": round(tp[t] / pred[t], 3) if pred[t] else 0.0,
                    "recall": round(tp[t] / support[t], 3) if support[t] else 0.0,
                    "support": support[t]} for t in TIERS}
    return {"n": len(dataset), "accuracy": round(correct / len(dataset), 3) if dataset else 0.0,
            "per_tier": per_tier}


def loo_embedding(dataset, k=K):
    """Leave-one-out k-NN over the dataset's own embeddings."""
    vecs = embed([r["task"] for r in dataset])
    tiers = [r["expected_tier"] for r in dataset]
    preds = []
    for i in range(len(dataset)):
        sims = sorted(((_cosine(vecs[i], vecs[j]), tiers[j])
                       for j in range(len(dataset)) if j != i), reverse=True)
        scores: dict = {}
        for sim, tier in sims[:k]:
            scores[tier] = scores.get(tier, 0.0) + sim
        preds.append(max(scores, key=scores.get) if scores else "MODERATE")
    return _acc_and_tiers(dataset, preds)


def main():
    prose = load_dataset(DS / "labeled_tasks_prose.jsonl")
    tuned = load_dataset(DS / "labeled_tasks.jsonl")
    router = ModelRouter(log_decisions=False)

    results = {}
    results["keyword (baseline)"] = evaluate(prose, router=router)  # router.classify = keyword

    # 9B-primary and 9B-hybrid — need a router actually wired to the classifier
    # model. Optional (needs the model up); never crash the run.
    try:
        from llmops import LocalLlamaClient, resolve_inference_config
        cfg = resolve_inference_config()
        clf_client = LocalLlamaClient(cfg["classifier_url"], cfg["classifier_model"],
                                      enable_thinking=False)
        r9 = ModelRouter(log_decisions=False, use_model_classifier=True,
                         classifier_client=clf_client)
        results["9B-primary"] = evaluate(prose, classify=lambda t: r9.classify_via_model(t)[0])
        results["9B-hybrid (prod)"] = evaluate(prose, classify=lambda t: r9.classify_hybrid(t)[0])
    except Exception as e:
        results["9B (unavailable)"] = {"error": str(e)[:80]}

    try:
        results[f"embedding LOO (k={K})"] = loo_embedding(prose)
        clf = EmbeddingClassifier(tuned, k=K)
        results[f"embedding XREF tuned->prose (k={K})"] = _acc_and_tiers(
            prose, [clf.classify(r["task"]) for r in prose])
    except Exception as e:
        results["embedding"] = {"error": str(e)[:120]}

    print(f"\nProse-blind set: n={len(prose)}  |  reference (tuned): n={len(tuned)}\n")
    for name, r in results.items():
        if "error" in r:
            print(f"  {name:<34} ERROR: {r['error']}")
            continue
        pt = " ".join(f"{t[0]}:{r['per_tier'][t]['recall']}" for t in TIERS)
        print(f"  {name:<34} acc={r['accuracy']:<6} (per-tier recall  {pt})")
    print()
    return results


if __name__ == "__main__":
    main()
