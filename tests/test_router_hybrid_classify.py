"""Keyword-first + 9B-rescue hybrid in ModelRouter.classify_hybrid (live routing)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmops import ModelRouter, CodingMemory

# Fires a COMPLEX keyword -> keyword is confident (matched=True).
KEYWORD_HIT = "fix the race condition causing intermittent crashes"
# No keyword rule fires -> keyword defaults to (MODERATE, False).
KEYWORD_MISS = "wibble the frobnitz across the zorptangle grubbly"


class _CountingLocal:
    """A classifier client that records how many times it's consulted."""
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0
    def complete(self, prompt, max_tokens=800, timeout=None):
        self.calls += 1
        return self.reply, {}


def _router(tmp_path, reply, use_model=True):
    return ModelRouter(memory=CodingMemory(tmp_path / "m.json"),
                       ledger=tmp_path / "e.jsonl", log_decisions=False,
                       classifier_client=_CountingLocal(reply),
                       use_model_classifier=use_model)


def test_confident_keyword_is_trusted_without_calling_9b(tmp_path):
    r = _router(tmp_path, reply="SIMPLE")   # 9B would (wrongly) say SIMPLE
    kw_tier, kw_matched = r.classify_detailed(KEYWORD_HIT)
    assert kw_matched is True               # precondition
    tier, matched = r.classify_hybrid(KEYWORD_HIT)
    assert (tier, matched) == (kw_tier, True)   # keyword wins, not the 9B's SIMPLE
    assert r.classifier_client.calls == 0       # 9B never consulted


def test_keyword_default_is_rescued_by_9b(tmp_path):
    r = _router(tmp_path, reply="COMPLEX")
    assert r.classify_detailed(KEYWORD_MISS)[1] is False   # precondition: defaulted
    tier, matched = r.classify_hybrid(KEYWORD_MISS)
    assert (tier, matched) == ("COMPLEX", True)            # 9B rescues the default
    assert r.classifier_client.calls == 1


def test_rescue_degrades_to_keyword_default_when_9b_unreachable(tmp_path):
    r = _router(tmp_path, reply="")          # empty reply -> ModelClassifier falls back
    tier, matched = r.classify_hybrid(KEYWORD_MISS)
    assert (tier, matched) == ("MODERATE", False)   # back to the keyword default
    assert r.classifier_client.calls == 1           # it was tried


def test_keyword_only_mode_never_calls_9b(tmp_path):
    r = _router(tmp_path, reply="SIMPLE", use_model=False)
    tier, matched = r.classify_hybrid(KEYWORD_MISS)
    assert (tier, matched) == ("MODERATE", False)
    assert r.classifier_client.calls == 0



def test_private_alias_preserved_for_back_compat(tmp_path):
    r = _router(tmp_path, reply="COMPLEX")
    assert r._classify(KEYWORD_MISS) == ("COMPLEX", True)  # same method, old name
