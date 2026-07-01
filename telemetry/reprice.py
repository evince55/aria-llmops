"""Re-derive `imputed_usd` on existing usage events using the CURRENT pricing.

`imputed_usd` is stamped at ingest time, so correcting `pricing.py` (e.g. the
Opus 4.8 $15/$75 -> $5/$25 fix) does NOT retroactively fix historical rows —
they keep the old, inflated values. `reprice()` recomputes `imputed_usd` from
each usage event's stored token counts.

Dry-run by default (reports the old vs new totals without touching the file);
pass ``write=True`` (CLI ``--write``) to rewrite the ledger atomically. Non-usage
events pass through untouched. Standard library only."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from telemetry import pricing, schema


def reprice_event(e: dict) -> tuple[dict, float]:
    """Return (event, delta_usd) with `imputed_usd` recomputed from stored token
    counts at current rates. Non-usage events are returned unchanged, delta 0."""
    if e.get("event") != "usage":
        return e, 0.0
    old = float(e.get("imputed_usd", 0.0) or 0.0)
    new = pricing.imputed_usd(
        e.get("model", ""),
        input_tokens=int(e.get("input_tokens", 0) or 0),
        output_tokens=int(e.get("output_tokens", 0) or 0),
        cache_write_tokens=int(e.get("cache_write_tokens", 0) or 0),
        cache_read_tokens=int(e.get("cache_read_tokens", 0) or 0),
    )
    return {**e, "imputed_usd": round(new, 6)}, round(new - old, 6)


def reprice(ledger: Path = schema.LEDGER_DEFAULT, write: bool = False) -> dict:
    """Recompute imputed_usd for every usage event in `ledger`.

    Returns a summary; only rewrites the file when `write` is True."""
    ledger = Path(ledger)
    events = schema.read_events(ledger)
    old_total = new_total = 0.0
    changed = 0
    repriced: list[dict] = []
    for e in events:
        updated, delta = reprice_event(e)
        repriced.append(updated)
        if e.get("event") == "usage":
            old_total += float(e.get("imputed_usd", 0.0) or 0.0)
            new_total += float(updated.get("imputed_usd", 0.0) or 0.0)
            if abs(delta) > 1e-9:
                changed += 1

    if write and events:
        # Atomic rewrite: write a sibling temp file, then replace.
        with tempfile.NamedTemporaryFile(
            "w", dir=str(ledger.parent), prefix=".events.", suffix=".tmp",
            delete=False, encoding="utf-8",
        ) as fh:
            tmp = Path(fh.name)
            for e in repriced:
                fh.write(json.dumps(e) + "\n")
        tmp.replace(ledger)

    return {
        "events": len(events),
        "usage_repriced": changed,
        "old_total_imputed_usd": round(old_total, 6),
        "new_total_imputed_usd": round(new_total, 6),
        "written": bool(write),
    }
