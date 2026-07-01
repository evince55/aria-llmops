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
    # Aggregate to the SESSION, not the event. `task_text` is a session's first
    # user message copied onto every assistant event, so classifying per event
    # counts one prompt once per message (a 1,100-event session would otherwise
    # dominate the tier distribution). Cost still sums across all events; the
    # unit of *classification* is the distinct session.
    sessions: dict = {}
    total_actual = 0.0
    total_imputed = 0.0
    n_usage_events = 0
    for e in events:
        if e.get("event") != "usage":
            continue
        task = e.get("task_text")
        if not task:
            continue
        n_usage_events += 1
        actual = float(e.get("actual_usd", 0.0) or 0.0)
        imputed = float(e.get("imputed_usd", 0.0) or 0.0)
        total_actual += actual
        total_imputed += imputed
        # Key by session_id; fall back to a per-event key so session-less events
        # aren't silently merged into one bucket.
        key = e.get("session_id") or f"_noid:{e.get('msg_id') or id(e)}"
        s = sessions.get(key)
        if s is None:
            s = sessions[key] = {"task": task, "imputed": 0.0, "actual": 0.0, "events": 0,
                                 "model": e.get("model")}
        s["imputed"] += imputed
        s["actual"] += actual
        s["events"] += 1

    rows = []
    by_complexity: dict = defaultdict(int)
    local_first = 0
    for key, s in sessions.items():
        tier = router.classify(s["task"])
        by_complexity[tier] += 1
        if tier in _LOCAL_FIRST_TIERS:
            local_first += 1
        rows.append({
            "session_id": key,
            "task": s["task"][:80],
            "actual_model": s["model"],
            "predicted_tier": tier,
            "events": s["events"],
            "imputed_usd": round(s["imputed"], 6),
        })
    n = len(sessions)
    return {
        "n_sessions": n,
        "n_usage_events": n_usage_events,
        "total_actual_usd": round(total_actual, 6),
        "total_imputed_usd": round(total_imputed, 6),
        # Fraction of SESSIONS whose router tier leads with a local/free model.
        # NOTE: this restates the TIER_PREFERENCE config as judged by the
        # classifier — it is NOT an output-quality measurement. A real
        # efficiency number needs a local-model capability probe (see the
        # 2026-07-01 routing/eval review).
        "local_first_sessions_pct": round(local_first / n * 100, 1) if n else 0.0,
        "by_complexity": dict(by_complexity),
        "rows": rows,
    }


def main() -> int:
    events = schema.read_events()
    print(json.dumps({k: v for k, v in evaluate(events).items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
