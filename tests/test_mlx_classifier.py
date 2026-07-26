"""Tests for the local-MLX classifier seam — deploying the promoted adapter.

S7's gate promoted `e2b_v3` as the router's rescue classifier, but the router
could only reach a classifier over HTTP (`LocalLlamaClient`), so the promoted
model could not actually be shipped. This seam serves it in-process: 3.2 GB
instead of 5.8 GB, and no remote endpoint to go down.

Two properties carry real risk and are pinned hardest:

* **Train/serve parity.** The adapter was fine-tuned on the chat-templated
  prompt. Serving it the raw string is textbook train/serve skew and would
  quietly cost accuracy that no test would otherwise catch.
* **Raw text out, not a tier.** `classify_finetuned.map_tier` resolves anything
  unparseable to MODERATE. If this client mapped tiers itself, a broken model
  would look like a confident MODERATE answer — re-creating the always-MODERATE
  artifact this project has hit three times, and blinding the degradation alarm
  added alongside it. The client returns raw text; `ModelClassifier` decides.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import ModelClassifier, resolve_inference_config  # noqa: E402
from mlx_classifier import MLXClassifierClient  # noqa: E402


class _Tok:
    """Tokenizer exposing apply_chat_template, like a real instruct tokenizer."""

    def apply_chat_template(self, msgs, add_generation_prompt=False, tokenize=False):
        return f"<|user|>{msgs[0]['content']}<|assistant|>"


class _BareTok:
    """A base tokenizer with no chat template."""


def _client(reply="COMPLEX", tok=None, calls=None):
    tok = tok if tok is not None else _Tok()

    def _load(model_path, adapter_path):
        (calls if calls is not None else []).append(("load", model_path, adapter_path))
        return ("MODEL", tok)

    def _generate(model, tokenizer, prompt, max_tokens):
        if calls is not None:
            calls.append(("generate", prompt, max_tokens))
        return reply

    return MLXClassifierClient("base/path", "adapter/path", _load=_load, _generate=_generate)


class TestCompleteContract:
    """Must be drop-in compatible with LocalLlamaClient's duck type."""

    def test_returns_text_and_usage_tuple(self):
        text, usage = _client("COMPLEX").complete("prompt")
        assert text == "COMPLEX"
        assert isinstance(usage, dict)

    def test_accepts_the_same_keyword_arguments_as_the_http_client(self):
        text, _ = _client("SIMPLE").complete("p", max_tokens=8, timeout=30, temperature=0.0)
        assert text == "SIMPLE"

    def test_honours_max_tokens(self):
        calls = []
        _client("SIMPLE", calls=calls).complete("p", max_tokens=13)
        assert ("generate", "<|user|>p<|assistant|>", 13) in calls


class TestTrainServeParity:
    def test_applies_the_chat_template_the_adapter_was_trained_with(self):
        calls = []
        _client("COMPLEX", calls=calls).complete("classify this")
        gen = [c for c in calls if c[0] == "generate"][0]
        assert gen[1] == "<|user|>classify this<|assistant|>", \
            "prompt was not chat-templated — train/serve skew"

    def test_falls_back_to_the_raw_prompt_without_a_template(self):
        calls = []
        _client("COMPLEX", tok=_BareTok(), calls=calls).complete("classify this")
        gen = [c for c in calls if c[0] == "generate"][0]
        assert gen[1] == "classify this"

    def test_a_broken_template_degrades_instead_of_raising(self):
        class _BadTok:
            def apply_chat_template(self, *a, **k):
                raise RuntimeError("template blew up")

        text, _ = _client("COMPLEX", tok=_BadTok()).complete("p")
        assert text == "COMPLEX"


class TestRawTextNotTier:
    def test_unparseable_reply_is_returned_verbatim_not_mapped_to_moderate(self):
        """The load-bearing one: mapping here would hide a broken model."""
        text, _ = _client("").complete("p")
        assert text == "", "empty reply was masked — the degradation signal is lost"

    def test_garbage_reply_is_returned_verbatim(self):
        text, _ = _client(";; the\n: to::").complete("p")
        assert text == ";; the\n: to::"

    def test_a_broken_mlx_model_still_trips_the_degradation_alarm(self):
        """End-to-end: the seam must not blunt the alarm built for the 9B."""
        clf = ModelClassifier(
            complete=lambda p, mt: _client("").complete(p, max_tokens=mt)[0],
            keyword_classify=lambda t: ("MODERATE", False),
            degrade_after=2)
        clf.classify("a")
        clf.classify("b")
        assert clf.fallback_rate() == 1.0
        assert clf.stats["model"] == 0


class TestModelIsLoadedOnce:
    def test_loads_once_across_many_completions(self):
        calls = []
        c = _client("SIMPLE", calls=calls)
        for _ in range(5):
            c.complete("p")
        assert len([x for x in calls if x[0] == "load"]) == 1

    def test_load_is_lazy_until_the_first_completion(self):
        """Constructing the router must not pull a 3 GB model into memory."""
        calls = []
        _client("SIMPLE", calls=calls)
        assert not calls, "model was loaded at construction, not on first use"


class TestBackendResolution:
    def test_default_is_auto_resolved_from_whether_the_adapter_exists(self):
        """Superseded 2026-07-25: the default WAS an unconditional "http". It is
        now auto-resolved, because the promoted adapter ties the incumbent at
        55% of the memory with no endpoint to fail — but only where it exists."""
        assert resolve_inference_config({}, _exists=lambda p: True)["classifier_backend"] == "mlx"
        assert resolve_inference_config({}, _exists=lambda p: False)["classifier_backend"] == "http"

    def test_mlx_backend_is_selectable_by_env(self):
        got = resolve_inference_config({"LLMOPS_CLASSIFIER_BACKEND": "mlx"})
        assert got["classifier_backend"] == "mlx"

    def test_backend_is_case_insensitive(self):
        assert resolve_inference_config(
            {"LLMOPS_CLASSIFIER_BACKEND": "MLX"})["classifier_backend"] == "mlx"

    def test_adapter_and_base_paths_come_from_env(self):
        got = resolve_inference_config({
            "LLMOPS_CLASSIFIER_BACKEND": "mlx",
            "LLMOPS_MLX_BASE": "/models/e2b",
            "LLMOPS_MLX_ADAPTER": "/adapters/e2b_v3",
        })
        assert got["mlx_base"] == "/models/e2b"
        assert got["mlx_adapter"] == "/adapters/e2b_v3"


class TestBackendAutoDetection:
    """The promoted adapter should be the DEFAULT where it exists — it ties the
    incumbent's accuracy at 55% of the memory and needs no remote endpoint,
    which is the single point of failure that took classification down for two
    days (a `--repeat-penalty 0` flag on the served 9B).

    But a blind flip would silently degrade every machine WITHOUT the adapter
    (CI, the homelab) to keyword-only routing. So the default is resolved from
    whether the adapter is actually present, and an explicit setting always wins.
    """

    def test_defaults_to_mlx_when_the_adapter_is_present(self):
        got = resolve_inference_config({}, _exists=lambda p: True)
        assert got["classifier_backend"] == "mlx"

    def test_falls_back_to_http_when_the_adapter_is_absent(self):
        got = resolve_inference_config({}, _exists=lambda p: False)
        assert got["classifier_backend"] == "http"

    def test_it_checks_the_ADAPTER_path_specifically(self):
        seen = []
        resolve_inference_config({"LLMOPS_MLX_ADAPTER": "/adapters/e2b_v3"},
                                 _exists=lambda p: seen.append(p) or True)
        assert "/adapters/e2b_v3" in seen

    def test_an_explicit_http_setting_beats_a_present_adapter(self):
        got = resolve_inference_config({"LLMOPS_CLASSIFIER_BACKEND": "http"},
                                       _exists=lambda p: True)
        assert got["classifier_backend"] == "http"

    def test_an_explicit_mlx_setting_beats_an_absent_adapter(self):
        """Explicit means explicit — fail loudly at load rather than silently
        routing keyword-only against the operator's stated intent."""
        got = resolve_inference_config({"LLMOPS_CLASSIFIER_BACKEND": "mlx"},
                                       _exists=lambda p: False)
        assert got["classifier_backend"] == "mlx"

    def test_auto_is_accepted_explicitly_too(self):
        assert resolve_inference_config({"LLMOPS_CLASSIFIER_BACKEND": "auto"},
                                        _exists=lambda p: True)["classifier_backend"] == "mlx"

    def test_an_unknown_value_resolves_to_auto_not_a_crash(self):
        assert resolve_inference_config({"LLMOPS_CLASSIFIER_BACKEND": "banana"},
                                        _exists=lambda p: True)["classifier_backend"] == "mlx"
