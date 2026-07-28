"""Serving a model through a template mode its training never saw.

Qwen3.5's chat template opens a reasoning block by default. The tool-call
training data contains no reasoning — prompt in, one JSON line out — so serving
with thinking ON asked the adapter to do something it was never trained for.
The same adapter on the same task emitted 661 tokens with thinking on and 21
with it off, and 11 of 19 truncated rows were graded CORRECT because the parser
harvested a draft from inside an unfinished generation (finding 17's mechanism).

The template mode is therefore part of the operating point, and — like
temperature in finding 19 — it must be NAMED and RECORDED rather than inherited.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.tool_call_eval import apply_template  # noqa: E402


class _Tok:
    """A tokenizer whose template accepts enable_thinking."""
    def apply_chat_template(self, messages, add_generation_prompt=True,
                            tokenize=False, enable_thinking=True):
        return f"[think={enable_thinking}]{messages[0]['content']}"


class _TokNoKwarg:
    """A tokenizer whose template does NOT accept enable_thinking (e.g. Gemma)."""
    def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=False):
        return f"[plain]{messages[0]['content']}"


class _TokNoTemplate:
    pass


class TestApplyTemplate:
    def test_thinking_is_passed_through_when_supported(self):
        text, applied = apply_template(_Tok(), "hi", thinking=False)
        assert "think=False" in text and applied is False

    def test_thinking_on_is_the_other_named_mode(self):
        text, applied = apply_template(_Tok(), "hi", thinking=True)
        assert "think=True" in text and applied is True

    def test_a_template_without_the_kwarg_degrades_and_reports_it(self):
        # Gemma has no thinking mode. Silently succeeding would let two arms be
        # served differently while the metadata claimed they matched.
        text, applied = apply_template(_TokNoKwarg(), "hi", thinking=False)
        assert text == "[plain]hi" and applied is None

    def test_a_tokenizer_without_a_template_falls_back_to_the_raw_prompt(self):
        text, applied = apply_template(_TokNoTemplate(), "hi", thinking=False)
        assert text == "hi" and applied is None

    def test_the_default_does_not_silently_choose_a_mode(self):
        # thinking=None means "do not pass the kwarg" — inheriting whatever the
        # template does, which is what went wrong here. It must be recorded.
        text, applied = apply_template(_Tok(), "hi", thinking=None)
        assert "think=True" in text and applied is None
