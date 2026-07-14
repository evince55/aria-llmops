"""Event constructors + an append-only, idempotent JSONL ledger."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# LLMOPS_LEDGER redirects ALL telemetry (CLIs, dashboard, runner, hook) — one
# env var isolates a demo/CI/smoke run from your real data. Read at import.
LEDGER_DEFAULT = Path(os.environ.get("LLMOPS_LEDGER") or (Path(__file__).parent / "events.jsonl"))

# Ledger-wide cap on stored task text. Enforced HERE, in the event
# constructors, so no caller can bloat the ledger: pre-fix, usage events were
# truncated by each caller by hand while make_route_decision_event stored the
# task verbatim — one multi-page routed prompt wrote its full text (probe:
# 20,000 chars) into every route_decision.
TASK_TEXT_MAX = 500


def _clip_task_text(task_text: Optional[str]) -> Optional[str]:
    if task_text is None:
        return None
    return str(task_text)[:TASK_TEXT_MAX]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_usage_event(
    *,
    harness: str,
    session_id: str,
    msg_id: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    cost_model: str = "subscription",
    actual_usd: float = 0.0,
    imputed_usd: float = 0.0,
    ts: Optional[str] = None,
    cwd: Optional[str] = None,
    git_branch: Optional[str] = None,
    task_text: Optional[str] = None,
    outcome: Any = None,
) -> dict:
    return {
        "event": "usage",
        "ts": ts or _now_iso(),
        "harness": harness,
        "session_id": session_id,
        "msg_id": msg_id,
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_write_tokens": int(cache_write_tokens),
        "cache_read_tokens": int(cache_read_tokens),
        "cost_model": cost_model,
        "actual_usd": round(float(actual_usd), 6),
        "imputed_usd": round(float(imputed_usd), 6),
        "cwd": cwd,
        "git_branch": git_branch,
        "task_text": _clip_task_text(task_text),
        "outcome": outcome,
    }


def make_route_decision_event(
    *,
    harness: str,
    task_text: str,
    complexity: str,
    chosen_model: str,
    estimated_usd: float,
    alternatives: list,
    session_id: Optional[str] = None,
    ts: Optional[str] = None,
) -> dict:
    return {
        "event": "route_decision",
        "ts": ts or _now_iso(),
        "harness": harness,
        # session_id links the decision to per-session outcome/usage events —
        # the join the flywheel harvester needs. None for legacy/sessionless
        # callers (the harvester falls back to a task-text prefix join).
        "session_id": session_id,
        "task_text": _clip_task_text(task_text),
        "complexity": complexity,
        "chosen_model": chosen_model,
        "estimated_usd": round(float(estimated_usd), 6),
        "alternatives": alternatives,
    }


def dedup_key(event: dict) -> Optional[str]:
    """Stable key for usage events; None for events that should always append."""
    if event.get("event") == "usage":
        return f"{event.get('harness')}|{event.get('session_id')}|{event.get('msg_id')}"
    return None


def _load_seen_keys(ledger: Path) -> set:
    seen: set = set()
    if not ledger.exists():
        return seen
    with ledger.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                k = dedup_key(json.loads(line))
            except ValueError:
                continue
            if k is not None:
                seen.add(k)
    return seen


def append_events(events: Iterable[dict], ledger: Path = LEDGER_DEFAULT) -> int:
    """Append events, skipping usage events whose dedup key already exists.
    Returns the number actually written."""
    ledger = Path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    seen = _load_seen_keys(ledger)
    appended = 0
    with ledger.open("a", encoding="utf-8") as fh:
        for e in events:
            k = dedup_key(e)
            if k is not None:
                if k in seen:
                    continue
                seen.add(k)
            fh.write(json.dumps(e) + "\n")
            appended += 1
    return appended


def read_events(ledger: Path = LEDGER_DEFAULT) -> list:
    ledger = Path(ledger)
    out = []
    if not ledger.exists():
        return out
    with ledger.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out
