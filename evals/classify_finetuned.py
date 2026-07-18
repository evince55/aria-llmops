"""Wrap a fine-tuned (or zero-shot) MLX model as a `classify(task) -> tier`
callable that plugs into the EXISTING eval harness unchanged:

    from evals.classify_finetuned import make_classifier
    from evals.router_classification_eval import evaluate, load_dataset
    classify = make_classifier(model_path, adapter_path)   # adapter_path=None => zero-shot
    evaluate(load_dataset(path), classify=classify)

This is stage 4 (S6 eval-gate) of the flywheel thin slice
(docs/specs/2026-07-17-flywheel-s5-thin-slice-design.md): the same harness scores
E2B-tuned, E4B-tuned, a base model untrained, and Bonsai zero-shot — one command
per model — so the bake-off is apples-to-apples.

Design notes
------------
* **Same rubric as production.** The classification prompt and the reply->tier
  mapping are lifted from ``llmops`` (``_CLASSIFY_PROMPT`` / ``_TIERS``) so the
  student sees the exact prompt the production 9B classifier and the minimax-m3
  teacher used — no drift, single source of truth.
* **Chat template applied here.** ``mlx_lm.generate`` does NOT apply the model's
  chat template to a string prompt; ``build_prompt`` applies it when the tokenizer
  supports one (needed for instruct bases and LoRA-tuned instruct models), and
  falls back to the raw prompt otherwise.
* **mlx is dev/training-only and lazily imported.** Importing this module does not
  import ``mlx_lm``; only the default load/generate seams do, at call time. The
  test-suite injects fakes for those seams, so it never loads a model or touches
  mlx. (``llmops.py`` itself never imports this module — the stdlib-runtime
  contract is unchanged.)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import _CLASSIFY_PROMPT, _TIERS  # noqa: E402  (shared rubric + tier order)
from evals.router_classification_eval import evaluate, load_dataset  # noqa: E402

_DATASETS_DIR = Path(__file__).parent / "datasets"
# The quarantined eval sets: human-labeled seed + keyword-blind prose (never a
# training input — see the spec's non-negotiables).
_EVAL_DATASETS = ("labeled_tasks.jsonl", "labeled_tasks_prose.jsonl")
_DEFAULT_MAX_TOKENS = 8      # a tier word is one/a few tokens; mirror ModelClassifier
_TASK_CHAR_CAP = 2000        # mirror ModelClassifier: cap the task in the prompt


# --------------------------------------------------------------------------- #
# Pure helpers (no mlx) — directly unit-tested
# --------------------------------------------------------------------------- #
def map_tier(reply) -> str:
    """Map a raw model reply to one of the four tiers.

    Substring match in ``_TIERS`` order (most-specific first:
    CRITICAL > COMPLEX > MODERATE > SIMPLE); the first tier present wins, so a
    messy reply like ``"moderate to complex"`` resolves to COMPLEX. Anything
    unparseable (empty, chatter, a non-string) falls back to MODERATE. This is
    the same mapping ``llmops.ModelClassifier.classify`` uses.
    """
    text = ("" if reply is None else str(reply)).strip().upper()
    for tier in _TIERS:
        if tier in text:
            return tier
    return "MODERATE"


def build_prompt(task: str, tokenizer=None) -> str:
    """Build the classification prompt for one task.

    Uses the production ``_CLASSIFY_PROMPT`` (full rubric + tier vocabulary so a
    zero-shot / untrained model can answer). When ``tokenizer`` exposes
    ``apply_chat_template`` the prompt is chat-templated with a generation prompt
    appended; otherwise (a base tokenizer, or a fake in tests) the raw prompt is
    returned. A misbehaving template degrades to the raw prompt rather than
    raising.
    """
    base = _CLASSIFY_PROMPT.format(task=str(task)[:_TASK_CHAR_CAP])
    apply = getattr(tokenizer, "apply_chat_template", None)
    if apply is None:
        return base
    try:
        return apply(
            [{"role": "user", "content": base}],
            add_generation_prompt=True,
            tokenize=False,
        )
    except Exception:  # pragma: no cover - defensive; tokenizer quirks
        return base


# --------------------------------------------------------------------------- #
# mlx seams (lazily imported; injected as fakes in tests)
# --------------------------------------------------------------------------- #
def _mlx_load(model_path, adapter_path):
    import mlx_lm  # lazy: dev/training-only dep, never imported at module load
    return mlx_lm.load(model_path, adapter_path=adapter_path)


def _mlx_generate(model, tokenizer, prompt, max_tokens):
    import mlx_lm  # lazy
    return mlx_lm.generate(
        model, tokenizer, prompt, verbose=False, max_tokens=max_tokens
    )


# --------------------------------------------------------------------------- #
# Public: build a classify(task) -> tier callable
# --------------------------------------------------------------------------- #
def make_classifier(
    model_path,
    adapter_path=None,
    *,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    _load=None,
    _generate=None,
):
    """Return ``classify(task) -> tier`` backed by an MLX model.

    ``adapter_path=None`` => ZERO-SHOT mode: evaluate a base/instruct model (or
    Bonsai) untrained through the same harness. A path => load base weights plus
    the LoRA adapter. The model is loaded once, here; the returned ``classify``
    reuses it for every task.

    ``_load`` / ``_generate`` are injectable seams (default to the lazily-imported
    ``mlx_lm.load`` / ``mlx_lm.generate``) so tests can exercise the full wiring
    without loading a real model or importing mlx.
    """
    load = _load or _mlx_load
    generate = _generate or _mlx_generate
    model, tokenizer = load(model_path, adapter_path)

    def classify(task: str) -> str:
        prompt = build_prompt(task, tokenizer)
        reply = generate(model, tokenizer, prompt, max_tokens)
        return map_tier(reply)

    return classify


# --------------------------------------------------------------------------- #
# Eval driver + CLI
# --------------------------------------------------------------------------- #
def run_eval(classify, datasets=_EVAL_DATASETS) -> dict:
    """Score ``classify`` over the quarantined eval sets.

    Returns ``{"union": <evaluate result over both sets>, "per_dataset":
    {name: <evaluate result>}}``. ``union`` is the honest overall instrument;
    the per-dataset split separates the keyword-tuned seed from the keyword-blind
    prose regime.
    """
    per_dataset = {}
    union: list = []
    for name in datasets:
        rows = load_dataset(_DATASETS_DIR / name)
        per_dataset[name] = evaluate(rows, classify=classify)
        union += rows
    return {"union": evaluate(union, classify=classify), "per_dataset": per_dataset}


def main(argv=None, *, classifier_factory=make_classifier) -> int:
    ap = argparse.ArgumentParser(
        description="Evaluate a fine-tuned or zero-shot MLX model as a router "
        "tier classifier on the quarantined labeled sets."
    )
    ap.add_argument("--model", required=True, help="path to the base MLX model")
    ap.add_argument(
        "--adapter",
        default=None,
        help="path to a LoRA adapter dir; omit for ZERO-SHOT evaluation",
    )
    ap.add_argument(
        "--max-tokens",
        type=int,
        default=_DEFAULT_MAX_TOKENS,
        help=f"generation cap per classification (default {_DEFAULT_MAX_TOKENS})",
    )
    args = ap.parse_args(argv)

    classify = classifier_factory(
        args.model, args.adapter, max_tokens=args.max_tokens
    )
    result = run_eval(classify)
    union = result["union"]
    summary = {
        "model": args.model,
        "adapter": args.adapter,
        "mode": "fine-tuned" if args.adapter else "zero-shot",
        "n": union["n"],
        "accuracy": union["accuracy"],
        "per_tier": union["per_tier"],
    }
    print(json.dumps({"summary": summary, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
