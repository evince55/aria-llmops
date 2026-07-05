import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from telemetry.outcomes import grade_outcome, _model_grade, _reaction_texts, outcome_from_transcript

FRAME = "do the thing"  # opening request; never itself a reaction


def test_keyword_verdict_wins_no_model_call():
    called = {"n": 0}
    def comp(p, mt):
        called["n"] += 1
        return "FAILURE"
    assert grade_outcome(["works now, ship it"], complete=comp) == "success"
    assert called["n"] == 0  # confident keyword -> model never consulted


def test_model_used_when_keyword_inconclusive():
    assert grade_outcome([FRAME, "yeah that'll do, thanks"], complete=lambda p, mt: "SUCCESS") == "success"
    assert grade_outcome([FRAME, "not what I wanted at all"], complete=lambda p, mt: "FAILURE") == "failure"


def test_model_unclear_and_error_return_none():
    assert grade_outcome([FRAME, "hmm"], complete=lambda p, mt: "UNCLEAR") is None
    def boom(p, mt):
        raise RuntimeError("down")
    assert grade_outcome([FRAME, "hmm"], complete=boom) is None


def test_no_complete_is_keyword_only():
    assert grade_outcome([FRAME, "yeah that'll do"], complete=None) is None
    assert grade_outcome(["perfect"], complete=None) == "success"


def test_framing_only_session_is_never_graded():
    """A single opening request has no reaction -> None, even if the model would
    (wrongly) call the problem statement a FAILURE. Model must not be consulted."""
    called = {"n": 0}
    def comp(p, mt):
        called["n"] += 1
        return "FAILURE"
    assert grade_outcome(["Harden data durability; fix the latent footgun"], complete=comp) is None
    assert called["n"] == 0


def test_harness_injected_turns_are_not_reactions():
    """Skill-preamble pseudo-user turns don't count as the user reacting."""
    texts = [FRAME, "Base directory for this skill: /x/y  # Brainstorming ..."]
    assert _reaction_texts(texts) == []  # injection stripped, framing dropped -> nothing left
    assert grade_outcome(texts, complete=lambda p, mt: "FAILURE") is None


def test_reaction_texts_drops_framing_keeps_rest():
    assert _reaction_texts([FRAME, "a", "b"]) == ["a", "b"]


def test_model_grade_parses_and_handles_empty():
    assert _model_grade([FRAME, "it fails"], lambda p, mt: "the answer is FAILURE") == "failure"
    assert _model_grade([], lambda p, mt: "SUCCESS") is None       # no texts -> None (no call)
    assert _model_grade([FRAME], lambda p, mt: "FAILURE") is None  # framing only -> None (no call)


def test_outcome_from_transcript_uses_model_when_given():
    lines = [
        {"type": "user", "message": {"content": "do the thing"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
        {"type": "user", "message": {"content": "yeah that'll do"}},  # reaction, no keyword signal
    ]
    assert outcome_from_transcript(lines, complete=lambda p, mt: "SUCCESS") == "success"
    assert outcome_from_transcript(lines) is None  # keyword-only can't decide
