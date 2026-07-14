"""Local-model capability probe — converts the Routing-SOL ceiling into an
expected-savings estimate (docs/research/2026-07-14-routing-sol-baseline.md §7.1).

The SOL bound assumes a confidently-classified frontier success would still
have succeeded on its tier's cheap chain-lead model — untested. This probe
tests it the only way available at solo-dev scale: select the top over-routed
sessions, replay each session's ORIGINATING TASK single-shot against a
local-tier model, and record the responses for rubric grading.

Deliberately NOT automated judging: the probe separates measurement (this
module: responses, latency, savings at stake) from judgment (a reviewer grades
pass/partial/fail against a rubric). Expected savings = grade-weighted savings.

Honesty notes baked into the design:
  - Single-shot task replay is a CAPABILITY signal, not an outcome replay —
    the original sessions were multi-turn and tool-using.
  - When the probe model is weaker than the tier's chain-lead (e.g. a 9B
    on-device proxy for the homelab 35B), passes are conservative evidence
    and failures are inconclusive for the chain-lead.

Run:
    LLMOPS_LOCAL_BASE_URL=... LLMOPS_LOCAL_MODEL=... LLMOPS_LOCAL_API_KEY=... \
    python3 -m evals.capability_probe [top_n]
Results land in evals/probe_results/ (gitignored — contains session task text).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import ModelRouter  # noqa: E402
from telemetry import pricing  # noqa: E402
from evals.routing_sol_eval import _aggregate_sessions  # noqa: E402

RESULTS_DIR = Path(__file__).parent / "probe_results"


def select_over_routed(events: list, router: ModelRouter | None = None,
                       top_n: int = 10) -> list[dict]:
    """Top over-routed sessions: outcome=success, confidently classified, with
    positive savings vs the tier's chain-lead reprice. Full task_text included
    (the SOL eval's report rows truncate it). Ranked by savings, descending."""
    router = router or ModelRouter(log_decisions=False)
    rows = []
    for sid, s in _aggregate_sessions(events).items():
        if s["outcome"] != "success":
            continue
        tier, confident = router.classify_hybrid(s["task"])
        if not confident:
            continue
        lead = router.preferences.get(tier, [None])[0]
        oracle = pricing.imputed_usd(lead, **s["tokens"]) if lead else s["usd"]
        savings = s["usd"] - oracle
        if savings <= 0:
            continue
        rows.append({
            "session_id": sid, "tier": tier, "chain_lead": lead,
            "actual_usd": round(s["usd"], 4), "oracle_usd": round(oracle, 4),
            "savings_usd": round(savings, 4), "task_text": s["task"],
        })
    rows.sort(key=lambda r: r["savings_usd"], reverse=True)
    return rows[:top_n]


def build_prompt(task_text: str) -> str:
    """Single-shot capability prompt: demand the concrete artifacts a coding
    session would need, so grading has something falsifiable to check."""
    return (
        "You are the local coding model in a routing cascade for an iOS music "
        "app project (Swift/SwiftUI app + FastAPI backend + Python LLMOps "
        "tooling). The following task came from a real session. Give your "
        "best concrete solution in one shot: (1) name the specific files/"
        "components you would change, (2) describe the approach in a few "
        "sentences, (3) show the key code change (diff or snippet). Be "
        "specific enough that a reviewer can judge whether your approach "
        "would have worked.\n\nTASK:\n" + task_text
    )


def run_probe(events: list, client, router: ModelRouter | None = None,
              top_n: int = 10, max_tokens: int = 600) -> list[dict]:
    """Run the probe against `client` (LocalLlamaClient-compatible: .model and
    .complete(prompt, max_tokens=, timeout=) -> (text, usage))."""
    out = []
    for row in select_over_routed(events, router=router, top_n=top_n):
        t0 = time.time()
        try:
            text, usage = client.complete(build_prompt(row["task_text"]),
                                          max_tokens=max_tokens, timeout=300.0)
        except Exception as exc:  # record the failure, keep probing
            text, usage = f"[probe error: {exc}]", {}
        out.append({
            **row,
            "model": getattr(client, "model", "?"),
            "response": text,
            "usage": usage,
            "latency_s": round(time.time() - t0, 2),
        })
    return out


def main() -> int:
    from llmops import LocalLlamaClient
    from telemetry import schema
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    client = LocalLlamaClient()  # LLMOPS_LOCAL_* env-driven
    # HYBRID selection is the point: the probe tests the 9B-rescued over-routed
    # pool, not just keyword-confident rows. Degrades to keyword-only when the
    # classifier endpoint (LLMOPS_CLASSIFIER_*) is unreachable.
    router = ModelRouter(log_decisions=False, use_model_classifier=True)
    rows = run_probe(schema.read_events(), client=client, router=router, top_n=top_n)
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    path = RESULTS_DIR / f"{stamp}-{client.model.replace('/', '_')}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(json.dumps({
        "probed": len(rows),
        "model": client.model,
        "savings_at_stake_usd": round(sum(r["savings_usd"] for r in rows), 4),
        "mean_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 1) if rows else 0,
        "results": str(path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
