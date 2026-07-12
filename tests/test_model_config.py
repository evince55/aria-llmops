"""LLMOPS_MODEL_CONFIG portability hook: map arbitrary models into the tiers
via a JSON file, with loud failures for broken configs. Offline."""
import json

import pytest

import llmops


@pytest.fixture
def _pristine():
    """Snapshot + restore the module-level tables the hook mutates."""
    rates = {k: dict(v) for k, v in llmops.MODEL_RATES.items()}
    prefs = {k: list(v) for k, v in llmops.TIER_PREFERENCE.items()}
    yield
    llmops.MODEL_RATES.clear()
    llmops.MODEL_RATES.update(rates)
    llmops.TIER_PREFERENCE.clear()
    llmops.TIER_PREFERENCE.update(prefs)


def _write(tmp_path, cfg):
    p = tmp_path / "models.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


def test_no_env_is_a_noop(monkeypatch, _pristine):
    monkeypatch.delenv("LLMOPS_MODEL_CONFIG", raising=False)
    before = dict(llmops.TIER_PREFERENCE)
    llmops._apply_model_config()
    assert llmops.TIER_PREFERENCE == before


def test_rates_merge_and_preferences_replace(monkeypatch, tmp_path, _pristine):
    path = _write(tmp_path, {
        "rates": {"llama-cpp/mine": {"input": 0, "output": 0}},
        "preferences": {"SIMPLE": ["llama-cpp/mine"]},
    })
    monkeypatch.setenv("LLMOPS_MODEL_CONFIG", path)
    llmops._apply_model_config()
    assert llmops.MODEL_RATES["llama-cpp/mine"] == {"input": 0.0, "output": 0.0}
    assert llmops.TIER_PREFERENCE["SIMPLE"] == ["llama-cpp/mine"]
    # partial mapping: unnamed tiers untouched
    assert llmops.TIER_PREFERENCE["CRITICAL"][0] == "opencode-go/minimax-m3"
    # built-in rates still present (merge, not replace)
    assert "llama-cpp/qwen35b" in llmops.MODEL_RATES


def test_router_actually_routes_to_configured_model(monkeypatch, tmp_path, _pristine):
    path = _write(tmp_path, {
        "rates": {"llama-cpp/mine": {"input": 0, "output": 0}},
        "preferences": {"SIMPLE": ["llama-cpp/mine"]},
    })
    monkeypatch.setenv("LLMOPS_MODEL_CONFIG", path)
    llmops._apply_model_config()
    r = llmops.ModelRouter(log_decisions=False)
    d = r.route_task("fix a typo in the readme")
    assert d["complexity"] == "SIMPLE"
    assert d["model"] == "llama-cpp/mine"


def test_unknown_tier_fails_loudly(monkeypatch, tmp_path, _pristine):
    path = _write(tmp_path, {"preferences": {"EPIC": ["llama-cpp/qwen35b"]}})
    monkeypatch.setenv("LLMOPS_MODEL_CONFIG", path)
    with pytest.raises(ValueError, match="unknown tier"):
        llmops._apply_model_config()


def test_missing_rate_fails_loudly(monkeypatch, tmp_path, _pristine):
    path = _write(tmp_path, {"preferences": {"SIMPLE": ["llama-cpp/unpriced"]}})
    monkeypatch.setenv("LLMOPS_MODEL_CONFIG", path)
    with pytest.raises(ValueError, match="no rates"):
        llmops._apply_model_config()


def test_empty_chain_fails_loudly(monkeypatch, tmp_path, _pristine):
    path = _write(tmp_path, {"preferences": {"SIMPLE": []}})
    monkeypatch.setenv("LLMOPS_MODEL_CONFIG", path)
    with pytest.raises(ValueError, match="non-empty"):
        llmops._apply_model_config()


def test_missing_file_fails_loudly(monkeypatch, _pristine):
    monkeypatch.setenv("LLMOPS_MODEL_CONFIG", "does/not/exist.json")
    with pytest.raises(OSError):
        llmops._apply_model_config()


def test_presets_parse_and_apply(monkeypatch, _pristine):
    """Every shipped preset must load cleanly — they are the community's entry point."""
    from pathlib import Path
    for preset in (Path(__file__).resolve().parents[1] / "configs").glob("*.json"):
        monkeypatch.setenv("LLMOPS_MODEL_CONFIG", str(preset))
        llmops._apply_model_config()
        for tier in ("SIMPLE", "MODERATE", "COMPLEX", "CRITICAL"):
            assert llmops.TIER_PREFERENCE[tier], preset.name
