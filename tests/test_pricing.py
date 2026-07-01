import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from telemetry import pricing


def test_imputed_cost_opus_basic():
    # 1,000,000 input @ $5 + 1,000,000 output @ $25 = $30
    assert pricing.imputed_usd("claude-opus-4-8", input_tokens=1_000_000, output_tokens=1_000_000) == 30.0


def test_imputed_cost_counts_cache_tokens():
    # cache read 1,000,000 @ $0.50 (0.1x of $5) = $0.50; cache write 1,000,000 @ $6.25 (1.25x) = $6.25
    got = pricing.imputed_usd("claude-opus-4-8", cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    assert got == round(0.50 + 6.25, 6)


def test_imputed_cost_fable_and_sonnet5_known():
    # fable-5 $10/$50; sonnet-5 $3/$15 — added defensively so ingests don't price to $0
    assert pricing.imputed_usd("claude-fable-5", input_tokens=1_000_000, output_tokens=1_000_000) == 60.0
    assert pricing.imputed_usd("claude-sonnet-5", input_tokens=1_000_000, output_tokens=1_000_000) == 18.0


def test_local_model_is_free():
    assert pricing.imputed_usd("llama-cpp/qwen35b", input_tokens=5_000_000, output_tokens=5_000_000) == 0.0


def test_unknown_model_is_zero():
    assert pricing.imputed_usd("totally-unknown", input_tokens=1_000_000) == 0.0
