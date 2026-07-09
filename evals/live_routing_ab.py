#!/usr/bin/env python3
"""LIVE local-routing A/B run — the first real execution data through the router.

What this produces (and why it exists)
--------------------------------------
Every other eval here is offline: classification is scored against labels, and
efficiency/quality replay *Claude Code* usage. None of them ever ran a task on
the local stack. This harness closes the loop live, on the single llama-swap
endpoint that actually serves both models:

  Arm A ("hybrid", the canonical run)
      ModelRouter.run_task() per task with the keyword-first + 9B-rescue
      classifier — the production configuration. Local-tier tasks EXECUTE on
      the 35B; route_decision + usage events land in the real ledger
      (harness="llmops-live"). This is the system doing its actual job.

  Arm B ("9b-primary", the comparison)
      The same tasks classified by the 9B alone (keyword only as unreachable-
      fallback), routed but NOT executed — we log the route_decision under
      harness="llmops-ab-9bprimary" and skip execution so the A/B doesn't
      double every 35B run. The arms differ in TIER choice; execution evidence
      comes from Arm A.

Then a human (or supervising agent) reviews each executed output and writes a
one-line REACTION; `grade` turns reactions into outcome labels via the same
grade_outcome path production uses (keyword first, 9B for inconclusive),
stamps them onto the ledger's usage events, and summarizes with
`telemetry.py eval quality`.

Honesty notes (read before quoting numbers)
-------------------------------------------
- N is SMALL (default 12 tasks). This is evidence, not statistics.
- The 9B is non-deterministic; its tier calls and grades can vary run to run.
- Reactions are authored by the reviewer of the outputs, recorded verbatim in
  the results file so anyone can audit label vs output.
- Wall times INCLUDE llama-swap model swap-in when the previous call used the
  other model. That's the real cost of this topology and is reported, not
  hidden.

Usage
-----
    python3 evals/live_routing_ab.py run            # arms A+B; writes records
    # ... review evals/live-runs/records.jsonl, author reactions ...
    python3 evals/live_routing_ab.py grade --reactions evals/live-runs/reactions.json
    python3 evals/live_routing_ab.py report         # print the final results

Standard library only. Endpoint/model come from the same env vars the router
uses, defaulting to the live llama-swap deployment (localhost:8080, keys
qwen3.6-35b / 9b-mythos).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import LocalLlamaClient, ModelRouter  # noqa: E402
from telemetry import schema  # noqa: E402
from telemetry.outcomes import grade_outcome  # noqa: E402

RUNS_DIR = Path(__file__).parent / "live-runs"
RECORDS = RUNS_DIR / "records.jsonl"
RESULTS = RUNS_DIR / "results.json"

# The live llama-swap deployment (verified 2026-07-09: GET /v1/models lists
# exactly these keys). Env-overridable like the router's own constants.
BASE_URL = os.environ.get("LLMOPS_LOCAL_BASE_URL",
                          os.environ.get("LLMOPS_SWAP_ENDPOINT", "http://localhost:8080/v1"))
EXEC_MODEL = os.environ.get("LLMOPS_LOCAL_MODEL", "qwen3.6-35b")
CLF_MODEL = os.environ.get("LLMOPS_CLASSIFIER_MODEL", "9b-mythos")

# Under llama-swap the 9B may need swapping in (~14s measured) — never time a
# classify out below that. Clamps the router's per-call timeout, which is
# hardcoded to 12s on main (fixed properly in the swap-aware-timeout PR).
MIN_CLASSIFY_TIMEOUT = float(os.environ.get("LLMOPS_MODEL_CALL_TIMEOUT", "45"))


class _SwapTolerantClient(LocalLlamaClient):
    """LocalLlamaClient whose EXPLICIT per-call timeouts can't drop below the
    measured llama-swap swap-in time. timeout=None keeps the client default —
    the first run of this harness clamped None down to the floor, which cut
    every 35B execution off at 45s (~800 tokens at ~8 t/s needs ~100s+)."""

    def complete(self, prompt, max_tokens=800, timeout=None):
        eff = self.timeout if timeout is None else max(timeout, MIN_CLASSIFY_TIMEOUT)
        return super().complete(prompt, max_tokens=max_tokens, timeout=eff)


class _NinePrimaryRouter(ModelRouter):
    """Arm B: the 9B is PRIMARY for every task (keyword only as the
    unreachable-fallback inside classify_via_model)."""

    def _classify(self, task):
        tier, source = self.classify_via_model(task)
        return tier, source == "model"


# 12 labeled tasks: 6 keyword-confident (from labeled_tasks.jsonl) + 6
# keyword-blind prose (from labeled_tasks_prose.jsonl), spanning all tiers.
TASKS = [
    # -- keyword-confident ----------------------------------------------------
    {"id": "kw-simple-rename",  "expected_tier": "SIMPLE",
     "task": "rename a variable in PlayerManager"},
    {"id": "kw-simple-test",    "expected_tier": "SIMPLE",
     "task": "write a unit test for the Debouncer"},
    {"id": "kw-mod-settings",   "expected_tier": "MODERATE",
     "task": "implement a new SwiftUI settings view"},
    {"id": "kw-mod-sleep",      "expected_tier": "MODERATE",
     "task": "wire up the sleep timer to pause playback"},
    {"id": "kw-cx-refactor",    "expected_tier": "COMPLEX",
     "task": "refactor the 1000-line PlayerManager god object"},
    {"id": "kw-crit-keychain",  "expected_tier": "CRITICAL",
     "task": "add encryption for stored credentials in the keychain"},
    # -- keyword-blind prose --------------------------------------------------
    {"id": "pr-crit-truncate",  "expected_tier": "CRITICAL",
     "task": "When two things try to save the user's library at the same instant, "
             "the file gets truncated and people permanently lose their playlists."},
    {"id": "pr-cx-mixer",       "expected_tier": "COMPLEX",
     "task": "Wire the new low-level audio-mixing engine into the streaming playback "
             "path so the equalizer and crossfade run on one shared signal chain."},
    {"id": "pr-cx-stall",       "expected_tier": "COMPLEX",
     "task": "Playback intermittently stalls when the network flaps mid-song; work out "
             "why the buffering coordinator gets stuck and rework it so it cannot."},
    {"id": "pr-mod-scrubber",   "expected_tier": "MODERATE",
     "task": "Show elapsed and remaining time on either side of the mini-player scrubber."},
    {"id": "pr-mod-eqpreset",   "expected_tier": "MODERATE",
     "task": "Remember the user's last-selected equalizer preset between app launches."},
    {"id": "pr-simple-tint",    "expected_tier": "SIMPLE",
     "task": "Give the now-playing screen a slightly warmer background tint."},
]


def _routers(ledger):
    # Executor: 35B swap-in (~30-60s) + ~800 tokens at ~8 t/s (~100s) — give it
    # a 300s ceiling so a slow cold start can't invalidate a run.
    exec_client = _SwapTolerantClient(base_url=BASE_URL, model=EXEC_MODEL, timeout=300.0)
    clf_client = _SwapTolerantClient(base_url=BASE_URL, model=CLF_MODEL)
    hybrid = ModelRouter(harness="llmops-live", ledger=ledger,
                         local_client=exec_client, classifier_client=clf_client,
                         use_model_classifier=True)
    nine = _NinePrimaryRouter(harness="llmops-ab-9bprimary", ledger=ledger,
                              local_client=exec_client, classifier_client=clf_client,
                              use_model_classifier=True)
    return hybrid, nine


def _append_record(rec: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with RECORDS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def cmd_run(args) -> int:
    ledger = Path(args.ledger) if args.ledger else schema.LEDGER_DEFAULT
    hybrid, nine = _routers(ledger)
    if RECORDS.exists() and not args.append:
        print(f"error: {RECORDS} exists; move it aside or pass --append", file=sys.stderr)
        return 2

    # ---- Arm B first: 9B-primary classification for every task. The 9B stays
    # resident throughout (llama-swap only swaps on model change), so these
    # walls approximate the RESIDENT cost of a 9B tier call.
    print(f"== Arm B (9b-primary): {len(TASKS)} classifications ==")
    for t in TASKS:
        t0 = time.time()
        decision = nine.route_task(t["task"], estimated_tokens=args.estimated_tokens)
        wall = round(time.time() - t0, 2)
        rec = {"arm": "9b-primary", "id": t["id"], "task": t["task"],
               "expected_tier": t["expected_tier"], "tier": decision["complexity"],
               "model": decision["model"], "estimated_cost": decision["estimated_cost"],
               "wall_s": wall, "executed": False,
               "note": "classification+routing only; execution evidence comes from the hybrid arm"}
        _append_record(rec)
        print(f"  {t['id']:<18} tier={decision['complexity']:<9} "
              f"model={decision['model']:<26} wall={wall}s")

    # ---- Arm A: the canonical closed loop. run_task executes local tiers on
    # the 35B and logs route_decision + usage to the ledger. Walls here INCLUDE
    # llama-swap swap-in whenever the previous call used the other model.
    print(f"== Arm A (hybrid, canonical): {len(TASKS)} run_task calls ==")
    for t in TASKS:
        before = len(schema.read_events(ledger=ledger))
        t0 = time.time()
        result = hybrid.run_task(t["task"], estimated_tokens=args.estimated_tokens,
                                 max_tokens=args.max_tokens)
        wall = round(time.time() - t0, 2)
        new = schema.read_events(ledger=ledger)[before:]
        session_id = next((e.get("session_id") for e in new if e.get("event") == "usage"), None)
        rec = {"arm": "hybrid", "id": t["id"], "task": t["task"],
               "expected_tier": t["expected_tier"], "tier": result["complexity"],
               "model": result["model"], "estimated_cost": result["estimated_cost"],
               "wall_s": wall, "executed": result.get("executed", False),
               "usage": result.get("usage"), "session_id": session_id,
               "output": result.get("output"), "error": result.get("error")}
        _append_record(rec)
        ex = "EXEC" if rec["executed"] else ("skip" if not result.get("error") else "ERR ")
        print(f"  {t['id']:<18} tier={result['complexity']:<9} "
              f"model={result['model']:<26} {ex} wall={wall}s")

    print(f"\nrecords -> {RECORDS}")
    print("Next: review each hybrid record's `output` and author "
          "evals/live-runs/reactions.json  ({record_id: one-line honest reaction}), "
          "then run the `grade` subcommand.")
    return 0


def _load_records() -> list:
    recs = []
    with RECORDS.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def _tier_metrics(recs: list) -> dict:
    n = len(recs)
    exact = sum(1 for r in recs if r["tier"] == r["expected_tier"])
    under = [r["id"] for r in recs
             if _rank(r["tier"]) < _rank(r["expected_tier"])]
    over = [r["id"] for r in recs
            if _rank(r["tier"]) > _rank(r["expected_tier"])]
    return {"n": n, "tier_accuracy": round(exact / n, 3) if n else 0.0,
            "under_provisioned": under, "over_provisioned": over}


_RANKS = {"SIMPLE": 0, "MODERATE": 1, "COMPLEX": 2, "CRITICAL": 3}


def _rank(tier: str) -> int:
    return _RANKS.get(tier, 1)


def cmd_grade(args) -> int:
    ledger = Path(args.ledger) if args.ledger else schema.LEDGER_DEFAULT
    reactions = json.loads(Path(args.reactions).read_text(encoding="utf-8"))
    recs = _load_records()
    hybrid = [r for r in recs if r["arm"] == "hybrid"]
    nine = [r for r in recs if r["arm"] == "9b-primary"]

    # 9B grader for reactions the keyword pass can't decide — same path
    # production uses. Fail-safe: unreachable -> keyword-only labels.
    clf = _SwapTolerantClient(base_url=BASE_URL, model=CLF_MODEL)
    complete = lambda p, mt: clf.complete(p, max_tokens=mt)[0]  # noqa: E731

    session_outcome = {}
    graded = []
    for r in hybrid:
        reaction = reactions.get(r["id"])
        outcome = None
        if r["executed"] and reaction:
            # grade_outcome expects ordered user turns: opening request first
            # (never graded as a reaction), then the reviewer's reaction.
            outcome = grade_outcome([r["task"], reaction], complete=complete)
        graded.append({**{k: r[k] for k in ("id", "tier", "expected_tier", "model",
                                            "executed", "wall_s", "session_id")},
                       "reaction": reaction, "outcome": outcome,
                       "usage": r.get("usage")})
        if outcome is not None and r.get("session_id"):
            session_outcome[r["session_id"]] = outcome

    # Stamp outcomes onto the ledger's usage events (atomic rewrite, same
    # pattern as telemetry.py backfill-outcomes — which only covers Claude Code
    # transcripts, not llmops-local sessions; this fills that gap for the run).
    events = schema.read_events(ledger=ledger)
    changed = 0
    for e in events:
        if e.get("event") != "usage":
            continue
        oc = session_outcome.get(e.get("session_id"))
        if oc is not None and e.get("outcome") != oc:
            e["outcome"] = oc
            changed += 1
    if changed:
        import tempfile
        with tempfile.NamedTemporaryFile("w", dir=str(ledger.parent), prefix=".events.",
                                         suffix=".tmp", delete=False, encoding="utf-8") as fh:
            tmp = Path(fh.name)
            for e in events:
                fh.write(json.dumps(e) + "\n")
        tmp.replace(ledger)

    # Summarize through the real CLI so the whole pipeline is exercised.
    quality = json.loads(subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "telemetry.py"),
         "eval", "quality", "--ledger", str(ledger)],
        capture_output=True, text=True, check=True).stdout)["quality"]

    exec_recs = [r for r in hybrid if r["executed"]]
    results = {
        "run_meta": {
            "endpoint": BASE_URL, "exec_model": EXEC_MODEL, "classifier_model": CLF_MODEL,
            "n_tasks": len(TASKS), "max_tokens": None,
            "honesty": ["small N — evidence, not statistics",
                        "9B tier calls and grades are non-deterministic",
                        "reactions are reviewer-authored quality judgments, recorded verbatim",
                        "wall times include llama-swap swap-in where applicable"],
        },
        "arm_hybrid": {**_tier_metrics(hybrid),
                       "executed": len(exec_recs),
                       "exec_wall_s": {
                           "min": min((r["wall_s"] for r in exec_recs), default=None),
                           "max": max((r["wall_s"] for r in exec_recs), default=None),
                           "mean": round(sum(r["wall_s"] for r in exec_recs) / len(exec_recs), 1)
                                   if exec_recs else None},
                       "records": graded},
        "arm_9b_primary": {**_tier_metrics(nine),
                           "records": [{k: r[k] for k in ("id", "tier", "expected_tier",
                                                          "model", "wall_s")}
                                       for r in nine]},
        "tier_disagreements": [
            {"id": h["id"], "hybrid": h["tier"], "nine_b": n9["tier"],
             "expected": h["expected_tier"]}
            for h, n9 in zip(sorted(hybrid, key=lambda r: r["id"]),
                             sorted(nine, key=lambda r: r["id"]))
            if h["tier"] != n9["tier"]],
        "ledger": {"events_outcome_stamped": changed},
        "quality_eval": quality,
    }
    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in results.items()
                      if k not in ("arm_hybrid", "arm_9b_primary")}, indent=2))
    print(f"\nfull results -> {RESULTS}")
    return 0


def cmd_report(args) -> int:
    print(RESULTS.read_text(encoding="utf-8"))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run both arms live; write records")
    r.add_argument("--ledger", help="override the ledger path")
    r.add_argument("--max-tokens", type=int, default=800)
    r.add_argument("--estimated-tokens", type=int, default=1000)
    r.add_argument("--append", action="store_true",
                   help="append to an existing records file")
    r.set_defaults(func=cmd_run)
    g = sub.add_parser("grade", help="grade reviewed outputs; stamp ledger; summarize")
    g.add_argument("--reactions", required=True,
                   help="JSON file: {record_id: one-line reaction}")
    g.add_argument("--ledger", help="override the ledger path")
    g.set_defaults(func=cmd_grade)
    rep = sub.add_parser("report", help="print the final results JSON")
    rep.set_defaults(func=cmd_report)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
