"""Focused eval for CRITICAL/severity detection — the router's one documented
blind spot (it maps data-loss / financial / security prose to COMPLEX/MODERATE).

Scores keyword / 9B-primary / 9B-hybrid on evals/datasets/labeled_severity.jsonl
(12 genuinely-CRITICAL severity tasks + 12 near-misses that contain CRITICAL
DOMAIN words but are not severe — so we measure precision, not just recall).

Reports overall accuracy, CRITICAL recall, CRITICAL precision, and the count of
non-CRITICAL tasks wrongly escalated to CRITICAL (over-provisioning cost).

Run from repo root: python3 evals/severity_eval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.router_classification_eval import evaluate, load_dataset  # noqa: E402
from llmops import ModelRouter  # noqa: E402

DS = Path(__file__).resolve().parent / "datasets" / "labeled_severity.jsonl"


def _false_critical(result) -> int:
    """Non-CRITICAL tasks predicted CRITICAL (over-provisioning)."""
    conf = result["confusion"]
    return sum(conf[a].get("CRITICAL", 0) for a in conf if a != "CRITICAL")


def strategies():
    rk = ModelRouter(log_decisions=False)
    out = [("keyword", lambda t: rk.classify(t))]
    try:
        from llmops import LocalLlamaClient, resolve_inference_config
        cfg = resolve_inference_config()
        cc = LocalLlamaClient(cfg["classifier_url"], cfg["classifier_model"], enable_thinking=False)
        r9 = ModelRouter(log_decisions=False, use_model_classifier=True, classifier_client=cc)
        out.append(("9B-primary", lambda t: r9.classify_via_model(t)[0]))
        out.append(("9B-hybrid", lambda t: r9.classify_hybrid(t)[0]))
    except Exception as e:
        print(f"(9B classifiers unavailable: {str(e)[:60]})")
    return out


def main():
    data = load_dataset(DS)
    ncrit = sum(1 for r in data if r["expected_tier"] == "CRITICAL")
    print(f"\nSeverity set: n={len(data)}  ({ncrit} CRITICAL, {len(data)-ncrit} near-miss non-CRITICAL)\n")
    print(f"  {'strategy':<12} {'overall':<8} {'CRIT-recall':<12} {'CRIT-prec':<11} false-CRIT")
    for name, clf in strategies():
        r = evaluate(data, classify=clf)
        c = r["per_tier"]["CRITICAL"]
        print(f"  {name:<12} {r['accuracy']:<8} {c['recall']:<12} {c['precision']:<11} {_false_critical(r)}")
    print()


if __name__ == "__main__":
    main()
