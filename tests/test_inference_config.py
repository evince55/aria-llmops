"""Pin the inference-topology defaults to the deployment that actually runs.

These constants are the router's contract with the local inference layer; a
silent drift here is exactly the failure mode that shipped stale 192.168.x
two-port defaults while the real deployment moved to a single llama-swap
endpoint (verified live 2026-07-09: GET /v1/models -> ["9b-mythos",
"qwen3.6-35b"], one chat completion per key on the one port).

resolve_inference_config is a pure function of an env mapping, so these tests
pin every topology/override combination without reloading the module or
touching the real environment.
"""
import llmops
from llmops import resolve_inference_config


# ---- swap mode (the default, live-verified topology) ------------------------

def test_default_mode_is_swap_single_endpoint():
    cfg = resolve_inference_config(env={})
    assert cfg["mode"] == "swap"
    # ONE endpoint fronts both models...
    assert cfg["local_url"] == cfg["classifier_url"] == "http://localhost:8080/v1"
    # ...routed by llama-swap model KEY (not gguf filename).
    assert cfg["local_model"] == "qwen3.6-35b"
    assert cfg["classifier_model"] == "9b-mythos"


def test_swap_endpoint_env_moves_both_clients():
    cfg = resolve_inference_config(env={"LLMOPS_SWAP_ENDPOINT": "http://10.0.0.5:9090/v1"})
    assert cfg["local_url"] == cfg["classifier_url"] == "http://10.0.0.5:9090/v1"
    # model keys unchanged by an endpoint move
    assert cfg["local_model"] == "qwen3.6-35b"
    assert cfg["classifier_model"] == "9b-mythos"


def test_unknown_mode_resolves_to_swap():
    cfg = resolve_inference_config(env={"LLMOPS_INFERENCE_MODE": "banana"})
    assert cfg["mode"] == "swap"


def test_mode_is_case_and_whitespace_tolerant():
    cfg = resolve_inference_config(env={"LLMOPS_INFERENCE_MODE": "  DUAL "})
    assert cfg["mode"] == "dual"


# ---- dual mode (legacy two-port layout, preserved) ---------------------------

def test_dual_mode_restores_legacy_two_port_layout():
    cfg = resolve_inference_config(env={"LLMOPS_INFERENCE_MODE": "dual"})
    assert cfg["mode"] == "dual"
    assert cfg["local_url"] == "http://192.168.1.84:8080/v1"
    assert cfg["local_model"] == "qwen3.6-35b-a3b-q8_k.gguf"
    assert cfg["classifier_url"] == "http://192.168.1.84:8081/v1"
    assert cfg["classifier_model"] == "9b_mythos_q8.gguf"


# ---- explicit env vars override the mode-derived defaults --------------------

def test_explicit_local_and_classifier_vars_win_in_swap_mode():
    cfg = resolve_inference_config(env={
        "LLMOPS_LOCAL_BASE_URL": "http://a:1/v1",
        "LLMOPS_LOCAL_MODEL": "m-exec",
        "LLMOPS_CLASSIFIER_BASE_URL": "http://b:2/v1",
        "LLMOPS_CLASSIFIER_MODEL": "m-clf",
    })
    assert cfg["local_url"] == "http://a:1/v1"
    assert cfg["local_model"] == "m-exec"
    assert cfg["classifier_url"] == "http://b:2/v1"
    assert cfg["classifier_model"] == "m-clf"


def test_explicit_vars_win_in_dual_mode_too():
    cfg = resolve_inference_config(env={
        "LLMOPS_INFERENCE_MODE": "dual",
        "LLMOPS_CLASSIFIER_MODEL": "override.gguf",
    })
    assert cfg["classifier_model"] == "override.gguf"
    assert cfg["local_model"] == "qwen3.6-35b-a3b-q8_k.gguf"  # untouched default


def test_explicit_local_url_beats_swap_endpoint_var():
    cfg = resolve_inference_config(env={
        "LLMOPS_SWAP_ENDPOINT": "http://swap:8080/v1",
        "LLMOPS_LOCAL_BASE_URL": "http://explicit:1/v1",
    })
    assert cfg["local_url"] == "http://explicit:1/v1"
    assert cfg["classifier_url"] == "http://swap:8080/v1"  # still the swap endpoint


# ---- module-level constants exist and came from the resolver -----------------

def test_module_constants_are_resolver_output():
    cfg = resolve_inference_config()
    assert llmops.INFERENCE_MODE == cfg["mode"]
    assert llmops.LOCAL_BASE_URL == cfg["local_url"]
    assert llmops.LOCAL_MODEL_NAME == cfg["local_model"]
    assert llmops.CLASSIFIER_BASE_URL == cfg["classifier_url"]
    assert llmops.CLASSIFIER_MODEL == cfg["classifier_model"]
