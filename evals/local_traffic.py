"""Generate real local-route traffic so the token split has something to measure.

A1 shipped the measurement and found there was nothing to measure: of 5,692
usage events essentially all were ingested Claude Code sessions, `llama-cpp/*`
had ~74 tokens logged in total, and the cloud routes are never executed by
`ModelRouter.run_task` (it only runs models named `llama-cpp/*`). So
`measured_output_ratio` had no evidence for any model the router actually uses,
`CostMonitor.estimate_cost` kept pricing off an unvalidated 0.4 assumption, and
A2's terse-output A/B would have compared two arms with no statistical content.

This drives real tasks through the production `run_task` path against a local
MLX server, so every call lands in the ledger with genuine prompt/completion
counts.

ARMS. `baseline` sends the task as-is; `terse` prepends the brevity clause A2
proposes. Each arm writes its own ledger, so the two are compared without
inventing a schema field, and the baseline arm doubles as A2's control rather
than being thrown away.

The task set is written here rather than drawn from `evals/datasets/` on
purpose: those are CLASSIFICATION instruments under quarantine, and reusing them
as execution prompts would blur what they are for. These are representative
local-route tasks — the SIMPLE/MODERATE work that `TIER_PREFERENCE` sends to the
local model — and they are labelled synthetic because they are.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llmops  # noqa: E402

# The brevity clause under test in A2. Kept verbatim from the research doc so
# the experiment measures the proposal, not a paraphrase of it.
TERSE_CLAUSE = (
    "Answer first, no preamble, no restating the task, no thinking-out-loud. "
    "Code, paths and errors byte-exact. Stop when the answer is complete.\n\n"
)

TASKS = (
    "Add a docstring to the debounce helper in utils/debounce.py explaining the delay argument.",
    "Rename the variable `res` to `response` in the fetch helper and update its two call sites.",
    "Write a Python function that parses an ISO-8601 timestamp and returns a datetime.",
    "Fix the off-by-one in this loop: for i in range(len(items) - 1): print(items[i])",
    "Add type hints to a function `def merge(a, b):` that merges two dicts, b winning ties.",
    "Write a regex that matches a semantic version like 1.22.3 and captures each part.",
    "Convert this callback-style function to async/await: fetchUser(id, cb).",
    "Add a --dry-run flag to an argparse CLI that currently has only --input and --output.",
    "Write a unit test for a function `slugify(title)` covering spaces, punctuation and unicode.",
    "Explain what `git rebase --onto A B C` does, in three sentences.",
    "Add a retry with exponential backoff around a requests.get call, max 3 attempts.",
    "Write a SQL query returning the 10 most recent orders per customer.",
    "Turn this shell one-liner into a readable script with error handling: curl $URL | jq .id",
    "Add a cache-control header of 1 hour to a FastAPI response.",
    "Write a function that chunks a list into batches of n, last batch possibly short.",
    "Given a JSON config with nested keys, write a helper `get(path, default)` using dotted paths.",
    "Add logging at INFO level around a function that uploads a file, including duration.",
    "Write a Makefile target `test` that runs pytest with coverage and fails under 80%.",
    "Refactor a 40-line function that validates a form into three smaller helpers.",
    "Add pagination (limit/offset) to a FastAPI endpoint that currently returns all rows.",
    "Write a debounce decorator for a Python function, delay configurable.",
    "Add a health endpoint to a Flask app returning status and version.",
    "Write a function to deep-merge two nested dicts without mutating either.",
    "Given a CSV with a header, write code that yields dicts per row using the stdlib.",
)


def build_router(ledger: Path):
    """Production router, pointed at whatever local endpoint the env configures."""
    return llmops.ModelRouter(ledger=ledger, log_decisions=False)


def run(arm: str, ledger: Path, limit: int = 0, max_tokens: int = 800,
        tasks=TASKS, router=None) -> dict:
    """Drive tasks through run_task, logging usage. Returns a run summary."""
    prefix = TERSE_CLAUSE if arm == "terse" else ""
    router = router or build_router(ledger)
    chosen = list(tasks)[:limit] if limit else list(tasks)
    done, failed, t0 = [], 0, time.time()
    for i, task in enumerate(chosen, 1):
        started = time.time()
        try:
            r = router.run_task(prefix + task, max_tokens=max_tokens, log_usage=True)
        except Exception as exc:  # one bad call must not lose the run
            print(f"  ! {i}/{len(chosen)} {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1
            continue
        u = r.get("usage") or {}
        # An executed-but-empty completion is the reasoning-preamble trap this
        # project keeps hitting: tokens are spent, no answer is produced. Count
        # it rather than letting it look like a clean run.
        empty = r.get("executed") and not (r.get("output") or "").strip()
        done.append({"task": task, "prompt": prefix + task, "model": r.get("model"),
                     "executed": bool(r.get("executed")),
                     # The ANSWER is kept: A2's quality gate grades these, and the
                     # first pass recorded only token counts, which left the arms
                     # ungradable and forced a re-run.
                     "output": r.get("output") or "",
                     "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
                     "empty_output": bool(empty), "seconds": round(time.time() - started, 2)})
        print(f"  {i:2d}/{len(chosen)} {r.get('model','?'):22s} "
              f"in {u.get('input_tokens',0):5d} out {u.get('output_tokens',0):5d} "
              f"{time.time()-started:5.1f}s{' EMPTY' if empty else ''}", file=sys.stderr)
    out_tok = sum(d["output_tokens"] for d in done)
    in_tok = sum(d["input_tokens"] for d in done)
    return {"arm": arm, "n": len(done), "failed": failed,
            "empty_outputs": sum(d["empty_output"] for d in done),
            "input_tokens": in_tok, "output_tokens": out_tok,
            "output_ratio_excl_cache": (out_tok / (in_tok + out_tok)) if (in_tok + out_tok) else 0.0,
            "mean_output_tokens": (out_tok / len(done)) if done else 0,
            "wall_seconds": round(time.time() - t0, 1),
            "mean_seconds": round(sum(d["seconds"] for d in done) / len(done), 2) if done else 0,
            "ledger": str(ledger), "rows": done}


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Generate local-route traffic (A1/A2)")
    p.add_argument("--arm", choices=("baseline", "terse"), default="baseline")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--max-tokens", type=int, default=800)
    p.add_argument("--ledger", default=None)
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)

    ledger = Path(a.ledger) if a.ledger else repo / f"telemetry/local_traffic_{a.arm}.jsonl"
    summary = run(a.arm, ledger, limit=a.limit, max_tokens=a.max_tokens)
    out = Path(a.out) if a.out else repo / f"logs/local_traffic_{a.arm}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
