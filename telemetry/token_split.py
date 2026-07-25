"""Input/output token split (A1) — the measurement brevity work depends on.

Output tokens cost 3-4x input on every cloud model priced in `llmops.MODEL_RATES`
(minimax-m3: $0.30 in / $1.20 out), so "being terse saves money" is unfalsifiable
without the split. A2 (terse-output A/B on the local build agent) and A3 (brevity
cap in the distill filter) are both measured against these numbers; without them
they are vibes.

READ THIS BEFORE QUOTING AN "OUTPUT RATIO"
------------------------------------------
With prompt caching the ratio is meaningless until the denominator is stated. In
this repo's own telemetry:

    input 874,069 · output 6,510,465 · cache_write 70,175,560 · cache_read 2,300,783,990

    out / (in + out)                 = 0.882
    out / (in + out + cache)         = 0.003

Same events, 300x apart. So both are computed, both are labelled, and they are
never averaged or silently swapped. `measured_output_ratio` uses the
**cache-exclusive** one on purpose: cached input is billed on its own line and is
driven by prompt reuse, not by how verbose a model is, so mixing it in would make
a "verbosity" metric track caching behaviour instead.

A caveat that belongs with any number this module produces: most events here come
from ingested Claude Code sessions, NOT from the router's own routed calls
(`llama-cpp/qwen35b` has ~74 tokens logged; the cloud routes are never executed by
`ModelRouter.run_task`, which only runs llama-cpp models). So these ratios
describe the harness, not the router, and must not be extrapolated onto
`CostMonitor.estimate_cost`'s models without routed-call data to back them.
"""
from __future__ import annotations

from collections import defaultdict

# The historical assumption baked into CostMonitor.estimate_cost. Kept as the
# documented fallback rather than a magic number, so a caller that has no
# measured data is at least explicit about what it is guessing.
DEFAULT_OUTPUT_RATIO = 0.4

_FIELDS = ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens")


def _usage(events):
    return [e for e in (events or []) if e.get("event") == "usage"]


def _n(row, key) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _ratios(i: int, o: int, cw: int, cr: int) -> tuple:
    excl = o / (i + o) if (i + o) else 0.0
    incl = o / (i + o + cw + cr) if (i + o + cw + cr) else 0.0
    return excl, incl


def split_totals(events) -> dict:
    """Totals for every token class, plus BOTH ratio definitions."""
    rows = _usage(events)
    t = {f: sum(_n(r, f) for r in rows) for f in _FIELDS}
    excl, incl = _ratios(t["input_tokens"], t["output_tokens"],
                         t["cache_write_tokens"], t["cache_read_tokens"])
    return {**t,
            "total_tokens": sum(t.values()),
            "output_ratio_excl_cache": excl,
            "output_ratio_incl_cache": incl,
            "n_events": len(rows)}


def by_model(events) -> list:
    """Per-model splits, heaviest first."""
    acc: dict = defaultdict(lambda: dict.fromkeys(_FIELDS, 0) | {"n_events": 0})
    for r in _usage(events):
        bucket = acc[r.get("model", "unknown")]
        for f in _FIELDS:
            bucket[f] += _n(r, f)
        bucket["n_events"] += 1
    out = []
    for model, b in acc.items():
        excl, incl = _ratios(b["input_tokens"], b["output_tokens"],
                             b["cache_write_tokens"], b["cache_read_tokens"])
        out.append({"model": model, **b,
                    "total_tokens": sum(b[f] for f in _FIELDS),
                    "output_ratio_excl_cache": excl,
                    "output_ratio_incl_cache": incl})
    return sorted(out, key=lambda r: -r["total_tokens"])


def per_session_output(events) -> dict:
    """Output tokens per SESSION — the spec asks for "per task", and session is
    the finest grouping these events actually carry (there is no task_id), so it
    is named for what it is rather than for what was wished for.

    Median is reported alongside the mean because one runaway agentic session
    otherwise sets the headline.
    """
    by_session: dict = defaultdict(int)
    for r in _usage(events):
        by_session[r.get("session_id", "unknown")] += _n(r, "output_tokens")
    vals = sorted(by_session.values())
    n = len(vals)
    median = 0 if not n else (vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2)
    return {"by_session": dict(by_session),
            "n_sessions": n,
            "mean": (sum(vals) / n) if n else 0,
            "median": median,
            "max": vals[-1] if n else 0}


def measured_output_ratio(events, model: str, *, min_events: int = 20,
                          default: float = DEFAULT_OUTPUT_RATIO) -> float:
    """Cache-exclusive output ratio for `model`, or `default` when the evidence
    is too thin to justify re-pricing on it.

    The sample floor is the whole point: two calls must not be allowed to move
    the router's cost model. Returns `default` for an unknown model, for fewer
    than `min_events` observations, and for a model whose logged tokens are all
    zero (a stub or a failed call).
    """
    rows = [r for r in _usage(events) if r.get("model") == model]
    if len(rows) < min_events:
        return default
    i = sum(_n(r, "input_tokens") for r in rows)
    o = sum(_n(r, "output_tokens") for r in rows)
    if (i + o) == 0:
        return default
    return o / (i + o)
