import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from telemetry import pricing


def test_imputed_cost_opus_basic():
    # 1,000,000 input @ $15 + 1,000,000 output @ $75 = $90
    assert pricing.imputed_usd("claude-opus-4-8", input_tokens=1_000_000, output_tokens=1_000_000) == 90.0


def test_imputed_cost_counts_cache_tokens():
    # cache read 1,000,000 @ $1.5 (0.1x of $15) = $1.5; cache write 1,000,000 @ $18.75 = $18.75
    got = pricing.imputed_usd("claude-opus-4-8", cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    assert got == round(1.5 + 18.75, 6)


def test_local_model_is_free():
    assert pricing.imputed_usd("llama-cpp/qwen35b", input_tokens=5_000_000, output_tokens=5_000_000) == 0.0


def test_unknown_model_is_zero():
    assert pricing.imputed_usd("totally-unknown", input_tokens=1_000_000) == 0.0
