"""The classifier must not degrade silently.

Written after a real incident (2026-07-23): a served 9B was REACHABLE but
returned empty/garbage completions. `ModelClassifier.classify` caught no
exception, matched no tier, and quietly returned a keyword answer for every
task. Routing silently reverted to keyword-only and nothing logged, counted, or
alerted — the eval harness has been bitten by degenerate models three times, and
this is the same class of failure on the *production* path.

Only the unreachable path warned. These tests pin the two gaps: an unparseable
reply must be visible, and SUSTAINED fallback must announce itself.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import ModelClassifier  # noqa: E402


def _kw(task):
    return ("MODERATE", False)


def _classifier(reply, **kw):
    return ModelClassifier(complete=lambda p, mt: reply, keyword_classify=_kw, **kw)


class TestUnparseableReplyIsVisible:
    def test_unparseable_reply_logs_a_warning(self, caplog):
        """The incident's exact shape: a reply arrives, no tier is in it."""
        c = _classifier("banana")
        with caplog.at_level(logging.WARNING, logger="llmops"):
            c.classify("x")
        assert any("keyword" in r.message.lower() for r in caplog.records), \
            "unparseable model reply fell back with no warning"

    def test_the_warning_shows_what_the_model_actually_said(self, caplog):
        """Without the offending text you cannot tell 'model is broken' from
        'model disagreed' — the incident took several probes to characterise."""
        c = _classifier(";; the\n: to:: to")
        with caplog.at_level(logging.WARNING, logger="llmops"):
            c.classify("x")
        assert any(";; the" in r.getMessage() for r in caplog.records)

    def test_empty_reply_is_reported_as_empty_not_omitted(self, caplog):
        c = _classifier("")
        with caplog.at_level(logging.WARNING, logger="llmops"):
            c.classify("x")
        assert caplog.records, "an empty completion is a degradation signal, not a no-op"

    def test_a_good_reply_logs_nothing(self, caplog):
        c = _classifier("COMPLEX")
        with caplog.at_level(logging.WARNING, logger="llmops"):
            assert c.classify("x") == ("COMPLEX", "model")
        assert not caplog.records


class TestFallbackAccounting:
    def test_counts_model_answers_and_fallbacks(self):
        c = _classifier("COMPLEX")
        c.classify("a")
        c.classify("b")
        assert c.stats == {"model": 2, "keyword-fallback": 0}

    def test_counts_fallbacks(self):
        c = _classifier("banana")
        c.classify("a")
        assert c.stats == {"model": 0, "keyword-fallback": 1}

    def test_fallback_rate_is_reported(self):
        c = ModelClassifier(
            complete=lambda p, mt: "COMPLEX" if "good" in p else "banana",
            keyword_classify=_kw)
        for t in ("good", "bad", "bad", "bad"):
            c.classify(t)
        assert c.fallback_rate() == 0.75

    def test_fallback_rate_of_an_unused_classifier_is_zero_not_a_crash(self):
        assert _classifier("COMPLEX").fallback_rate() == 0.0


class TestSustainedDegradation:
    """A single fallback is normal (models disagree). EVERY call falling back
    means the model is broken — that is the alarm the incident needed."""

    def test_sustained_fallback_announces_degradation(self, caplog):
        c = _classifier("banana", degrade_after=3)
        with caplog.at_level(logging.ERROR, logger="llmops"):
            for _ in range(3):
                c.classify("x")
        assert any("degrad" in r.getMessage().lower() for r in caplog.records), \
            "a wholly broken classifier never announced itself"

    def test_does_not_cry_wolf_below_the_threshold(self, caplog):
        c = _classifier("banana", degrade_after=3)
        with caplog.at_level(logging.ERROR, logger="llmops"):
            c.classify("x")
            c.classify("x")
        assert not caplog.records

    def test_occasional_fallback_never_triggers_it(self, caplog):
        """Healthy traffic mixes both; only a collapse should alarm."""
        c = ModelClassifier(
            complete=lambda p, mt: "banana" if "bad" in p else "SIMPLE",
            keyword_classify=_kw, degrade_after=3)
        with caplog.at_level(logging.ERROR, logger="llmops"):
            for t in ("good", "bad", "good", "bad", "good", "bad"):
                c.classify(t)
        assert not caplog.records

    def test_announces_once_not_on_every_subsequent_call(self, caplog):
        """A broken endpoint serves thousands of tasks; one alarm, not a flood."""
        c = _classifier("banana", degrade_after=2)
        with caplog.at_level(logging.ERROR, logger="llmops"):
            for _ in range(10):
                c.classify("x")
        alarms = [r for r in caplog.records if "degrad" in r.getMessage().lower()]
        assert len(alarms) == 1

    def test_recovery_rearms_the_alarm(self, caplog):
        """If the model comes back and breaks again, the second outage must
        also be reported — otherwise one alarm per process hides everything."""
        replies = iter(["banana", "banana", "SIMPLE", "banana", "banana"])
        c = ModelClassifier(complete=lambda p, mt: next(replies),
                            keyword_classify=_kw, degrade_after=2)
        with caplog.at_level(logging.ERROR, logger="llmops"):
            for _ in range(5):
                c.classify("x")
        alarms = [r for r in caplog.records if "degrad" in r.getMessage().lower()]
        assert len(alarms) == 2
