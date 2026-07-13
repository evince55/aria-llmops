"""Batch execute-and-judge: scale the outcome-labelled half of the ledger.

Routes every task in a labeled dataset through the production router; tasks that
route LOCAL are actually executed on the local model, then judged SUCCESS/FAILURE
by the local classifier model against a fixed rubric. Each executed task writes
ONE outcome-labelled usage event (harness=llmops-judged-local); route_decisions
log for every task, executed or not. A full run report (config, judge provenance,
per-task records, aggregates) is written to evals/results/.

HONESTY — read before citing these numbers:
  Outcomes here are judged by a local ~9B model, not by a human. Treat them as
  WEAK labels: good for volume, trend, and failure-mode mining; not equivalent
  to the human-graded n=12 live run (evals/live-runs/). The judge model, prompt,
  and temperature are recorded in the report so the grading is reproducible and
  criticisable. Cohort is isolated under its own harness name — every eval and
  dashboard view can include or exclude it deliberately.

Run from repo root (needs the local endpoint up):
  python3 evals/batch_execute_judged.py [--dataset labeled_tasks_balanced.jsonl]
                                        [--limit N] [--max-tokens 1024]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from llmops import LocalLlamaClient, ModelRouter, resolve_inference_config  # noqa: E402
from telemetry import schema  # noqa: E402
from telemetry.pricing import imputed_usd  # noqa: E402
from evals.router_classification_eval import load_dataset  # noqa: E402

HARNESS = "llmops-judged-local"
JUDGE_MAX_TOKENS = 8
JUDGE_PROMPT = (
    "You are grading whether an AI coding assistant's response plausibly addresses a task.\n"
    "TASK:\n{task}\n\nRESPONSE (may be truncated):\n{output}\n\n"
    "Reply with exactly one word.\n"
    "SUCCESS = the response is an on-topic, substantive attempt at the task.\n"
    "FAILURE = the response is empty, off-topic, refuses, or was cut off before "
    "saying anything substantive.\nOne word:"
)


def build():
    cfg = resolve_inference_config()
    judge = LocalLlamaClient(cfg["classifier_url"], cfg["classifier_model"],
                             enable_thinking=False)
    router = ModelRouter(log_decisions=True, harness=HARNESS,
                         use_model_classifier=True, classifier_client=judge)
    return router, judge, cfg


def judge_outcome(judge: LocalLlamaClient, task: str, output: str):
    """SUCCESS/FAILURE from the judge model; (None, raw) when unparseable."""
    prompt = JUDGE_PROMPT.format(task=task[:1500], output=(output or "")[:3000])
    try:
        text, _ = judge.complete(prompt, max_tokens=JUDGE_MAX_TOKENS, temperature=0.0)
    except Exception as e:  # judge down != execution invalid; record as ungraded
        return None, f"judge error: {str(e)[:80]}"
    word = (text or "").strip().upper()
    if word.startswith("SUCCESS"):
        return "success", text.strip()
    if word.startswith("FAILURE"):
        return "failure", text.strip()
    return None, (text or "").strip()[:80]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default="labeled_tasks_balanced.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="0 = all tasks")
    ap.add_argument("--max-tokens", type=int, default=1024,
                    help="execution output cap (the n=12 run's 800 capped 7/11 outputs)")
    ap.add_argument("--out", default=None, help="report path override")
    args = ap.parse_args()

    data = load_dataset(REPO / "evals" / "datasets" / args.dataset)
    if args.limit:
        data = data[:args.limit]
    router, judge, cfg = build()
    run_id = f"judged-{uuid.uuid4().hex[:8]}"
    print(f"[{run_id}] {len(data)} tasks from {args.dataset}; "
          f"executor={cfg['local_model']} judge={cfg['classifier_model']} "
          f"max_tokens={args.max_tokens}", flush=True)

    records = []
    t_start = time.time()
    for i, item in enumerate(data):
        task = item["task"]
        t0 = time.time()
        # log_usage=False: THIS harness writes the (outcome-labelled) usage event.
        r = router.run_task(task, max_tokens=args.max_tokens, log_usage=False)
        wall = round(time.time() - t0, 1)
        rec = {
            "task": task, "expected_tier": item.get("expected_tier"),
            "tier": r.get("complexity"), "model": r.get("model"),
            "executed": bool(r.get("executed")), "wall_s": wall,
            "in_tok": (r.get("usage") or {}).get("input_tokens"),
            "out_tok": (r.get("usage") or {}).get("output_tokens"),
            "outcome": None, "judge_raw": None,
            "exec_error": r.get("error"),
        }
        if rec["executed"]:
            outcome, raw = judge_outcome(judge, task, r.get("output"))
            rec["outcome"], rec["judge_raw"] = outcome, raw
            try:
                schema.append_events([schema.make_usage_event(
                    harness=HARNESS, session_id=run_id, msg_id=f"t{i}",
                    model=rec["model"], input_tokens=rec["in_tok"] or 0,
                    output_tokens=rec["out_tok"] or 0, cost_model="local",
                    imputed_usd=imputed_usd(rec["model"],
                                            input_tokens=rec["in_tok"] or 0,
                                            output_tokens=rec["out_tok"] or 0),
                    task_text=task, outcome=outcome)])
            except Exception as e:
                print(f"  !! ledger write failed t{i}: {e}", flush=True)
        records.append(rec)
        print(f"  [{i+1}/{len(data)}] {rec['tier']:<8} exec={rec['executed']} "
              f"outcome={rec['outcome']} wall={wall}s", flush=True)

    executed = [r for r in records if r["executed"]]
    judged = [r for r in executed if r["outcome"] in ("success", "failure")]
    succ = sum(1 for r in judged if r["outcome"] == "success")
    by_tier = {}
    for r in executed:
        d = by_tier.setdefault(r["tier"], {"executed": 0, "success": 0, "failure": 0, "ungraded": 0})
        d["executed"] += 1
        d[r["outcome"] if r["outcome"] in ("success", "failure") else "ungraded"] += 1
    walls = sorted(r["wall_s"] for r in executed) or [0]
    cap_hits = sum(1 for r in executed if (r["out_tok"] or 0) >= args.max_tokens)

    report = {
        "run_id": run_id, "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset, "n_tasks": len(records),
        "config": {"executor_model": cfg["local_model"], "judge_model": cfg["classifier_model"],
                   "max_tokens": args.max_tokens, "judge_prompt": JUDGE_PROMPT,
                   "judge_temperature": 0.0},
        "honesty": ("Outcomes are model-judged (local ~9B), NOT human-graded. Weak labels: "
                    "use for volume/trends/failure mining; the human-graded baseline is the "
                    "n=12 live run in evals/live-runs/."),
        "aggregates": {
            "executed": len(executed), "judged": len(judged), "success": succ,
            "failure": len(judged) - succ,
            "judged_success_rate": round(succ / len(judged), 3) if judged else None,
            "by_tier": by_tier,
            "wall_s": {"min": walls[0], "median": walls[len(walls) // 2], "max": walls[-1]},
            "tokens": {"in": sum(r["in_tok"] or 0 for r in executed),
                       "out": sum(r["out_tok"] or 0 for r in executed)},
            "output_cap_hits": cap_hits,
            "total_wall_s": round(time.time() - t_start, 1),
        },
        "records": records,
    }
    out = Path(args.out) if args.out else (
        REPO / "evals" / "results" / f"judged_run_{datetime.now(timezone.utc):%Y-%m-%d}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    a = report["aggregates"]
    print(f"\n[{run_id}] done in {a['total_wall_s']}s: executed {a['executed']}/{len(records)}, "
          f"judged success rate {a['judged_success_rate']} "
          f"({a['success']}/{a['judged']}), cap hits {cap_hits}\nwrote {out}", flush=True)


if __name__ == "__main__":
    main()
