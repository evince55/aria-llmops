"""Tests for evals/classify_finetuned.py — the fine-tuned/zero-shot MLX model
wrapper that plugs into router_classification_eval.evaluate(classify=...).

These tests NEVER load a real model or touch mlx: the mlx load/generate calls
are injected as fakes. They lock down (a) the reply->tier mapping including messy
replies, (b) the classify() wiring (prompt built from the task, generate called,
reply mapped), (c) zero-shot vs fine-tuned adapter plumbing, and (d) that the
resulting classify() plugs into the existing eval harness unchanged.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals import classify_finetuned as cf  # noqa: E402
from evals import router_classification_eval as ev  # noqa: E402

TIERS = {"SIMPLE", "MODERATE", "COMPLEX", "CRITICAL"}


# --------------------------------------------------------------------------- #
# map_tier: reply -> tier
# --------------------------------------------------------------------------- #
def test_map_tier_clean_words_each_case():
    for tier in TIERS:
        assert cf.map_tier(tier) == tier
        assert cf.map_tier(tier.lower()) == tier
        assert cf.map_tier(f"  {tier.title()}\n") == tier


def test_map_tier_messy_replies():
    assert cf.map_tier("The tier is COMPLEX.") == "COMPLEX"
    assert cf.map_tier("i'd say simple") == "SIMPLE"
    assert cf.map_tier("Answer: Moderate") == "MODERATE"
    assert cf.map_tier("**CRITICAL** — data loss") == "CRITICAL"
    assert cf.map_tier("tier=simple\n\n") == "SIMPLE"


def test_map_tier_unparseable_defaults_moderate():
    for junk in ["", "   ", "banana", "I don't know", "42", "\n\t"]:
        assert cf.map_tier(junk) == "MODERATE", junk


def test_map_tier_non_string_is_safe():
    # A model wrapper may hand back None or a non-str object; must not raise.
    assert cf.map_tier(None) == "MODERATE"
    assert cf.map_tier(123) == "MODERATE"


def test_map_tier_precedence_most_specific_first():
    # Mirrors llmops.ModelClassifier: iterate _TIERS (CRITICAL>COMPLEX>MODERATE>
    # SIMPLE); the most-specific tier present wins regardless of word order.
    assert cf.map_tier("moderate to complex") == "COMPLEX"
    assert cf.map_tier("simple or critical") == "CRITICAL"
    assert cf.map_tier("could be simple, maybe moderate") == "MODERATE"


def test_map_tier_matches_production_mapping():
    # The student must map replies exactly as the production model classifier
    # does. _TIERS is the shared source of truth.
    from llmops import _TIERS
    assert cf._TIERS == _TIERS


# --------------------------------------------------------------------------- #
# build_prompt
# --------------------------------------------------------------------------- #
def test_build_prompt_contains_task_and_rubric():
    p = cf.build_prompt("wire up the search endpoint")
    assert "wire up the search endpoint" in p
    # zero-shot needs the rubric vocabulary so an untrained model can answer
    for tier in TIERS:
        assert tier in p


def test_build_prompt_truncates_long_task():
    task = "A" * 3000 + "ZZZTAIL"
    p = cf.build_prompt(task, tokenizer=None)
    assert "ZZZTAIL" not in p  # capped well under 3000 chars
    assert "A" * 100 in p


def test_build_prompt_applies_chat_template_when_available():
    calls = {}

    class FakeTok:
        def apply_chat_template(self, messages, add_generation_prompt=False, tokenize=True):
            calls["messages"] = messages
            calls["add_generation_prompt"] = add_generation_prompt
            calls["tokenize"] = tokenize
            return "<CHAT>" + messages[0]["content"] + "<END>"

    p = cf.build_prompt("refactor the queue", tokenizer=FakeTok())
    assert p.startswith("<CHAT>") and p.endswith("<END>")
    assert "refactor the queue" in p
    assert calls["add_generation_prompt"] is True
    assert calls["tokenize"] is False
    assert calls["messages"][0]["role"] == "user"


def test_build_prompt_falls_back_without_chat_template():
    p = cf.build_prompt("rename a symbol", tokenizer=object())  # no apply_chat_template
    assert "rename a symbol" in p
    assert not p.startswith("<CHAT>")


def test_build_prompt_survives_template_error():
    class BadTok:
        def apply_chat_template(self, *a, **k):
            raise RuntimeError("boom")

    p = cf.build_prompt("add a button", tokenizer=BadTok())
    assert "add a button" in p  # raw prompt fallback, no raise


# --------------------------------------------------------------------------- #
# make_classifier: wiring (fakes injected — no mlx)
# --------------------------------------------------------------------------- #
def _fake_load(records):
    def load(model_path, adapter_path):
        records["load"] = (model_path, adapter_path)
        return ("MODEL_OBJ", "TOK_OBJ")
    return load


def test_make_classifier_zero_shot_passes_none_adapter():
    rec = {}
    cf.make_classifier("some/model", _load=_fake_load(rec),
                       _generate=lambda *a, **k: "SIMPLE")
    assert rec["load"] == ("some/model", None)


def test_make_classifier_finetuned_passes_adapter():
    rec = {}
    cf.make_classifier("base/model", "adapters/e2b", _load=_fake_load(rec),
                       _generate=lambda *a, **k: "SIMPLE")
    assert rec["load"] == ("base/model", "adapters/e2b")


def test_classify_wires_generate_and_maps_reply():
    gen_calls = []

    def fake_generate(model, tokenizer, prompt, max_tokens):
        gen_calls.append((model, tokenizer, prompt, max_tokens))
        return "This looks COMPLEX to me"

    classify = cf.make_classifier(
        "m", None, max_tokens=8,
        _load=lambda mp, ap: ("MODEL_OBJ", "TOK_OBJ"),
        _generate=fake_generate,
    )
    assert classify("optimize the render loop") == "COMPLEX"
    assert len(gen_calls) == 1
    model, tok, prompt, mt = gen_calls[0]
    assert model == "MODEL_OBJ" and tok == "TOK_OBJ"
    assert "optimize the render loop" in prompt
    assert mt == 8


def test_classify_returns_only_valid_tiers():
    classify = cf.make_classifier(
        "m", None,
        _load=lambda mp, ap: ("M", None),
        _generate=lambda *a, **k: "wat",  # unparseable -> MODERATE
    )
    assert classify("whatever") in TIERS
    assert classify("whatever") == "MODERATE"


def test_make_classifier_forwards_custom_max_tokens():
    seen = {}

    def fake_generate(model, tokenizer, prompt, max_tokens):
        seen["mt"] = max_tokens
        return "MODERATE"

    classify = cf.make_classifier(
        "m", None, max_tokens=32,
        _load=lambda mp, ap: ("M", None), _generate=fake_generate,
    )
    classify("t")
    assert seen["mt"] == 32


# --------------------------------------------------------------------------- #
# Interface match: plugs into the existing eval harness
# --------------------------------------------------------------------------- #
def test_classify_plugs_into_router_classification_eval():
    dataset = [
        {"task": "rename a var", "expected_tier": "SIMPLE"},
        {"task": "refactor concurrency", "expected_tier": "COMPLEX"},
        {"task": "add an endpoint", "expected_tier": "MODERATE"},
    ]

    # Fake model that "reads" the TASK (not the rubric, which itself contains
    # words like "concurrency"/"endpoint") and echoes a plausible tier word.
    def fake_generate(model, tokenizer, prompt, max_tokens):
        tail = prompt.rsplit("Task:", 1)[-1]  # the task text follows the final "Task:"
        if "concurrency" in tail:
            return "COMPLEX"
        if "rename" in tail:
            return "SIMPLE"
        return "MODERATE"

    classify = cf.make_classifier(
        "m", None, _load=lambda mp, ap: ("M", None), _generate=fake_generate,
    )
    res = ev.evaluate(dataset, classify=classify)
    assert res["n"] == 3
    assert res["accuracy"] == 1.0
    assert set(res["per_tier"]).issubset(TIERS)
    assert "confusion" in res


# --------------------------------------------------------------------------- #
# run_eval + CLI (real datasets, fake classifier — no mlx)
# --------------------------------------------------------------------------- #
def test_run_eval_reports_union_and_per_dataset():
    res = cf.run_eval(classify=lambda t: "MODERATE")
    assert "union" in res and "per_dataset" in res
    assert set(res["per_dataset"]) == {"labeled_tasks.jsonl", "labeled_tasks_prose.jsonl"}
    u = res["union"]
    # union n == sum of the two quarantined sets (24 + 18 = 42)
    assert u["n"] == sum(d["n"] for d in res["per_dataset"].values())
    assert 0.0 <= u["accuracy"] <= 1.0
    assert set(u["per_tier"]).issubset(TIERS)


def test_cli_main_prints_accuracy_and_per_tier(capsys):
    # Inject a fake classifier factory so the CLI never touches mlx.
    def fake_factory(model_path, adapter_path=None, *, max_tokens=8):
        assert model_path == "path/to/model"
        assert adapter_path == "path/to/adapter"
        return lambda t: "MODERATE"

    rc = cf.main(
        ["--model", "path/to/model", "--adapter", "path/to/adapter"],
        classifier_factory=fake_factory,
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["mode"] == "fine-tuned"
    assert out["summary"]["model"] == "path/to/model"
    assert "accuracy" in out["summary"]
    assert "per_tier" in out["union"]


def test_cli_zero_shot_mode_without_adapter(capsys):
    def fake_factory(model_path, adapter_path=None, *, max_tokens=8):
        assert adapter_path is None
        return lambda t: "SIMPLE"

    rc = cf.main(["--model", "base/model"], classifier_factory=fake_factory)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["mode"] == "zero-shot"
    assert out["summary"]["adapter"] is None


# --------------------------------------------------------------------------- #
# Contract: mlx must NOT be imported at module load (dev/training-only, lazy)
# --------------------------------------------------------------------------- #
def test_mlx_is_not_imported_at_module_top_level():
    src = Path(cf.__file__).read_text(encoding="utf-8")
    for line in src.splitlines():
        if re.match(r"^\s*(import\s+mlx|from\s+mlx)", line):
            assert line[:1].isspace(), f"mlx imported at top level: {line!r}"
