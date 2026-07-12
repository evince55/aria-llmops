import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmops import ModelClassifier, ModelRouter, CodingMemory


def _kw(task):
    return ("MODERATE", False)


def test_model_classifier_parses_tier():
    c = ModelClassifier(complete=lambda p, mt: "COMPLEX", keyword_classify=_kw)
    assert c.classify("x") == ("COMPLEX", "model")


def test_model_classifier_trims_and_uppercases():
    c = ModelClassifier(complete=lambda p, mt: "  simple\n", keyword_classify=_kw)
    assert c.classify("x") == ("SIMPLE", "model")


def test_model_classifier_falls_back_on_garbage():
    c = ModelClassifier(complete=lambda p, mt: "banana", keyword_classify=lambda t: ("CRITICAL", True))
    assert c.classify("x") == ("CRITICAL", "keyword-fallback")


def test_model_classifier_falls_back_on_error():
    def boom(p, mt):
        raise RuntimeError("down")
    c = ModelClassifier(complete=boom, keyword_classify=lambda t: ("SIMPLE", True))
    assert c.classify("x") == ("SIMPLE", "keyword-fallback")


class _FakeLocal:
    def __init__(self, reply):
        self.reply = reply
    def complete(self, prompt, max_tokens=800, timeout=None, temperature=0.2):
        return self.reply, {}


def _router(tmp_path, reply):
    mem = CodingMemory(tmp_path / "mem.json")
    return ModelRouter(memory=mem, ledger=tmp_path / "e.jsonl", log_decisions=False,
                       classifier_client=_FakeLocal(reply), use_model_classifier=True)


def test_router_uses_model_classifier(tmp_path):
    tier, source = _router(tmp_path, "COMPLEX").classify_via_model("ambiguous multi-paragraph task")
    assert tier == "COMPLEX" and source == "model"


def test_route_task_with_model_classifier(tmp_path):
    dec = _router(tmp_path, "SIMPLE").route_task("some task the keywords would miss")
    assert dec["complexity"] == "SIMPLE"


def test_route_task_model_unreachable_falls_back_to_keywords(tmp_path):
    # empty model reply -> keyword fallback on the real text
    dec = _router(tmp_path, "").route_task("refactor the audio engine for performance")
    assert dec["complexity"] == "COMPLEX"
