"""Serve the promoted tier classifier locally, in-process, via MLX.

S7's promotion gate cleared `e2b_v3` as the router's rescue classifier, but the
router could only reach a classifier over HTTP (`llmops.LocalLlamaClient`), so
the promoted model had nowhere to run. This client exposes the same duck type
backed by a local MLX model + LoRA adapter:

    router = ModelRouter(classifier_client=MLXClassifierClient(base, adapter),
                         use_model_classifier=True)

Why bother: the tuned 3.2 GB E2B matched or beat the 5.8 GB keyword+9B hybrid on
every tier, and running it in-process removes the remote endpoint entirely — the
single point of failure that took classification down on 2026-07-23.

`llmops.py` stays stdlib-only at runtime. mlx_lm is a dev/inference-only
dependency imported lazily inside the seams below, and `llmops` only imports
this module when the classifier backend is explicitly set to "mlx".

TWO PROPERTIES ARE LOAD-BEARING — see tests/test_mlx_classifier.py:

1. **Chat-template parity.** The adapter was fine-tuned on the chat-templated
   prompt, so serving it a raw string is train/serve skew that costs accuracy
   silently. `complete()` templates whatever prompt it is handed.
2. **Raw text out, never a mapped tier.** `classify_finetuned.map_tier` resolves
   anything unparseable to MODERATE. Mapping here would make a broken model look
   like a confident MODERATE answer — the always-MODERATE artifact this project
   has been bitten by three times — and would blind
   `ModelClassifier`'s degradation alarm. Tier parsing belongs to the caller.
"""
from __future__ import annotations

import logging

LOG = logging.getLogger("llmops")

_DEFAULT_MAX_TOKENS = 8  # a tier word is one/a few tokens; mirrors ModelClassifier


def _mlx_load(model_path, adapter_path):
    import mlx_lm  # lazy: inference-only dep, never imported at module load
    return mlx_lm.load(model_path, adapter_path=adapter_path)


def _mlx_generate(model, tokenizer, prompt, max_tokens):
    import mlx_lm  # lazy
    return mlx_lm.generate(model, tokenizer, prompt, verbose=False,
                           max_tokens=max_tokens)


class MLXClassifierClient:
    """Local MLX stand-in for `llmops.LocalLlamaClient`.

    Implements only what the classifier path uses: `.complete(prompt, ...)`
    returning `(text, usage)`. The model is loaded on FIRST USE, not at
    construction, so building a router stays cheap for callers that never reach
    the model (keyword-only routing, tests, `--help`).
    """

    def __init__(self, model_path, adapter_path=None, *,
                 max_tokens: int = _DEFAULT_MAX_TOKENS, _load=None, _generate=None) -> None:
        self.model_path = model_path
        self.adapter_path = adapter_path
        self.max_tokens = max_tokens
        self._load = _load or _mlx_load
        self._generate = _generate or _mlx_generate
        self._model = None
        self._tokenizer = None

    def _ensure_loaded(self):
        if self._model is None:
            LOG.info("loading MLX classifier %s (adapter=%s)",
                     self.model_path, self.adapter_path)
            self._model, self._tokenizer = self._load(self.model_path, self.adapter_path)
        return self._model, self._tokenizer

    def _templated(self, prompt: str) -> str:
        """Apply the tokenizer's chat template — the form the adapter was tuned
        on. A base tokenizer (or a quirky template) degrades to the raw prompt
        rather than raising, matching evals/classify_finetuned.build_prompt."""
        apply = getattr(self._tokenizer, "apply_chat_template", None)
        if apply is None:
            return prompt
        try:
            return apply([{"role": "user", "content": prompt}],
                         add_generation_prompt=True, tokenize=False)
        except Exception:  # pragma: no cover - defensive; tokenizer quirks
            return prompt

    def complete(self, prompt: str, max_tokens: int | None = None,
                 timeout: float | None = None, temperature: float = 0.0):
        """Return `(text, usage)`.

        `timeout` and `temperature` are accepted for interface parity with the
        HTTP client and ignored: generation is local (nothing to time out) and
        the classifier always wants greedy decoding.
        """
        model, _ = self._ensure_loaded()
        text = self._generate(model, self._tokenizer, self._templated(prompt),
                              self.max_tokens if max_tokens is None else max_tokens)
        # Raw text, deliberately unmapped — see the module docstring.
        return ("" if text is None else str(text)), {}
