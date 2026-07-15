import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from telemetry.outcomes import outcome_from_transcript, outcome_from_user_texts


def _u(text):
    return {"type": "user", "message": {"content": text}}


def _a(text):
    return {"type": "assistant", "message": {"content": text}}


SKILL = ("Base directory for this skill: /Users/x/.claude/skills/tdd\n"
         "Write the test first. Test proves fix and prevents regression. "
         "Never fix bugs without a failing test.")
SYSD = ("Base directory for this skill: /Users/x/.claude/skills/debug\n"
        "4. If fix doesn't work - stop and return to Phase 1.")


def test_skill_body_failure_words_do_not_score_failure():
    # Skill-injected turns ("prevents regression", "if fix doesn't work") must
    # NOT be read as the user reporting a failure.
    # Skill turns come LAST, so a naive last-decisive keyword scan would take
    # their "regression"/"doesn't work" as the verdict. Filtering must win.
    lines = [_u("Add a settings tab to the library view"),
             _a("done"),
             _u("great, that works — merge it"),
             _u(SKILL),
             _u(SYSD)]
    assert outcome_from_transcript(lines) == "success"


def test_framing_request_mentioning_broken_is_not_a_failure():
    # The opening request is the GOAL, not a reaction. "fix the broken playback"
    # with no negative follow-up must not score failure.
    lines = [_u("Fix the broken/dead playback features in the Aria app"),
             _a("fixed")]
    assert outcome_from_transcript(lines) is None


def test_only_skill_turns_after_framing_yield_no_verdict():
    lines = [_u("Refactor the queue manager"), _a("ok"), _u(SYSD)]
    assert outcome_from_transcript(lines) is None


def test_conditional_if_clause_is_not_a_failure():
    # A conditional instruction is not a complaint about completed work.
    assert outcome_from_user_texts(
        ["if this ip doesn't work, try the windows desktop"]) is None
    # ...but a plain failure report still scores failure (contract preserved).
    assert outcome_from_user_texts(["this doesn't work"]) == "failure"


def test_real_reaction_failure_still_detected_through_transcript():
    lines = [_u("Fix the crash on startup"), _a("try this"),
             _u("nope, still broken after your change")]
    assert outcome_from_transcript(lines) == "failure"
