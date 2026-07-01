"""Model pricing (USD per 1M tokens) and imputed-cost calculation.

Covers Claude models (for Claude Code usage events) and the opencode/local
models from llmops.MODEL_RATES. Cache tokens are priced separately: reads are
cheap (~0.1x input), writes/creation cost a premium (~1.25x input).

NOTE: Claude list rates below should be sanity-checked against current public
pricing via the `claude-api` skill. The math is rate-table-driven, so updating
a number here is the only change needed.
"""
from __future__ import annotations

PRICING: dict[str, dict[str, float]] = {
    # Claude list API rates (USD / 1M tokens). cache_write = 1.25x input (5-min
    # TTL), cache_read = 0.1x input. Verified against the claude-api skill 2026-07-01.
    "claude-opus-4-8":   {"input": 5.0,  "output": 25.0, "cache_write": 6.25,  "cache_read": 0.50},
    "claude-fable-5":    {"input": 10.0, "output": 50.0, "cache_write": 12.5,  "cache_read": 1.0},
    "claude-sonnet-5":   {"input": 3.0,  "output": 15.0, "cache_write": 3.75,  "cache_read": 0.30},
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0, "cache_write": 3.75,  "cache_read": 0.30},
    "claude-haiku-4-5":  {"input": 1.0,  "output": 5.0,  "cache_write": 1.25,  "cache_read": 0.10},
    # opencode / local (mirror of llmops.MODEL_RATES; local self-hosted = free)
    "opencode-go/minimax-m3":          {"input": 0.30, "output": 1.20},
    "opencode/deepseek-v4-flash":      {"input": 0.14, "output": 0.28},
    "opencode/qwen3.7-plus":           {"input": 0.40, "output": 1.60},
    "opencode/deepseek-v4-flash-free": {"input": 0.0,  "output": 0.0},
    "llama-cpp/qwen35b":               {"input": 0.0,  "output": 0.0},
}


def imputed_usd(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Return the list-rate USD cost for the given token counts, or 0.0 for an
    unknown/free model. cache_write/cache_read fall back to the input rate when
    a model doesn't price them separately."""
    rate = PRICING.get(model)
    if rate is None:
        return 0.0
    cw = rate.get("cache_write", rate["input"])
    cr = rate.get("cache_read", rate["input"])
    total = (
        input_tokens * rate["input"]
        + output_tokens * rate["output"]
        + cache_write_tokens * cw
        + cache_read_tokens * cr
    )
    return round(total / 1_000_000, 6)
