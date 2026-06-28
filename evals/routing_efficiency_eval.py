"""Replay real usage events through ModelRouter to estimate routing efficiency.

For each task we observed actually running (on Opus, under the Max subscription),
ask: what tier would the router assign, and would a local/cheaper model have
plausibly sufficed? Cost-only — no output-quality judgment."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import ModelRouter  # noqa: E402
from telemetry import schema  # noqa: E402

# Tiers whose preference chain leads with a local/free model -> "local would do".
_LOCAL_FIRST_TIERS = {"SIMPLE", "MODERATE", "COMPLEX"}


def evaluate(events: list, router: ModelRouter | None = None) -> dict:
    router = router or ModelRouter(log_decisions=False)
    rows = []
    by_complexity: dict = defaultdict(int)
    total_actual = 0.0
    total_imputed = 0.0
    would_local = 0
    for e in events:
        if e.get("event") and e.get("event") != "usage":
            continue
        task = e.get("task_text")
        if not task:
            continue
        tier = router.classify(task)
        by_complexity[tier] += 1
        actual = float(e.get("actual_usd", 0.0) or 0.0)
        imputed = float(e.get("imputed_usd", 0.0) or 0.0)
        total_actual += actual
        total_imputed += imputed
        local_ok = tier in _LOCAL_FIRST_TIERS
        if local_ok:
            would_local += 1
        rows.append({
            "task": task[:80],
            "actual_model": e.get("model"),
            "predicted_tier": tier,
            "imputed_usd": round(imputed, 6),
            "local_would_suffice": local_ok,
        })
    n = len(rows)
    return {
        "n_tasks": n,
        "total_actual_usd": round(total_actual, 6),
        "total_imputed_usd": round(total_imputed, 6),
        "would_route_local_pct": round(would_local / n * 100, 1) if n else 0.0,
        "by_complexity": dict(by_complexity),
        "rows": rows,
    }


def main() -> int:
    events = schema.read_events()
    print(json.dumps({k: v for k, v in evaluate(events).items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
