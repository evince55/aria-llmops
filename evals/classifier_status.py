"""Run the tier classifiers across every labeled dataset and write a status
JSON the dashboard reads, so classifier accuracy is visible + refreshable.

Evaluates keyword (offline, always) and the 9B-hybrid production classifier
(needs the model) on: labeled_tasks_prose (honest OOD), labeled_tasks_balanced
(the balanced set), labeled_severity (CRITICAL focus). Writes
evals/results/classifier_status.json.

Run from repo root: python3 evals/classifier_status.py [--no-model]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.router_classification_eval import evaluate, load_dataset  # noqa: E402
from llmops import ModelRouter  # noqa: E402

DS = Path(__file__).resolve().parent / "datasets"
OUT = Path(__file__).resolve().parent / "results" / "classifier_status.json"
DATASETS = {
    "prose_blind": ("labeled_tasks_prose.jsonl", "real ledger prose — honest OOD benchmark"),
    "balanced": ("labeled_tasks_balanced.jsonl", "balanced, diverse (35B-generated + audited)"),
    "severity": ("labeled_severity.jsonl", "CRITICAL/severity focus (recall + precision)"),
}


def _model_router():
    from llmops import LocalLlamaClient, resolve_inference_config
    cfg = resolve_inference_config()
    cc = LocalLlamaClient(cfg["classifier_url"], cfg["classifier_model"], enable_thinking=False)
    return ModelRouter(log_decisions=False, use_model_classifier=True, classifier_client=cc)


def main():
    use_model = "--no-model" not in sys.argv
    kw = ModelRouter(log_decisions=False)
    r9 = _model_router() if use_model else None
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "datasets": {}}
    for key, (fname, desc) in DATASETS.items():
        path = DS / fname
        if not path.exists():
            continue
        data = load_dataset(path)
        entry = {"description": desc, "n": len(data),
                 "keyword": evaluate(data, classify=lambda t: kw.classify(t))}
        if r9 is not None:
            try:
                entry["model_hybrid"] = evaluate(data, classify=lambda t: r9.classify_hybrid(t)[0])
            except Exception as e:
                entry["model_hybrid_error"] = str(e)[:100]
        result["datasets"][key] = entry
        acc_kw = entry["keyword"]["accuracy"]
        acc_m = entry.get("model_hybrid", {}).get("accuracy", "-")
        print(f"  {key:<12} n={len(data):<3} keyword={acc_kw}  9B-hybrid={acc_m}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
