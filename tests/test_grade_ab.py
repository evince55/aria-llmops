"""Tests for A2's quality gate — blind A/B grading of the two arms.

The promotion gate scores TIER CLASSIFICATION and cannot answer this: A2 changes
what the build agent WRITES, so the thing to grade is answer quality.

Three properties carry the risk and are pinned hardest:

* **Blind + position-randomised.** LLM judges have a documented position bias, so
  the arms are shuffled per task and unmapped afterwards. A grader that can see
  which arm is which is grading the hypothesis, not the answer.
* **Independent grader.** Not the model under test, not the local model — the
  same rule the eval-set labelling follows.
* **The known bias runs AGAINST the treatment.** LLM judges tend to prefer longer
  answers, and the treatment is brevity. That makes a terse win conservative, and
  a terse loss ambiguous — stated rather than hidden.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.grade_ab import (  # noqa: E402
    QUALITY_TOLERANCE, build_prompt, decide, grade, parse_verdict, tally,
)


def _pair(task="t", base="baseline answer", terse="terse answer"):
    return {"task": task, "baseline": base, "terse": terse}


class TestBlindPrompt:
    def test_prompt_contains_both_answers_and_the_task(self):
        p = build_prompt("write a chunker", "AAA", "BBB")
        assert "write a chunker" in p and "AAA" in p and "BBB" in p

    def test_prompt_never_names_the_arms(self):
        """If the judge can tell which is the treatment, it is not blind."""
        p = build_prompt("t", "AAA", "BBB").lower()
        for leak in ("baseline", "terse", "brevity", "treatment", "control"):
            assert leak not in p, f"prompt leaks the arm identity: {leak!r}"

    def test_prompt_asks_for_correctness_and_completeness(self):
        p = build_prompt("t", "a", "b").lower()
        assert "correct" in p and "complete" in p


class TestParseVerdict:
    def test_parses_plain_json(self):
        v = parse_verdict('{"winner":"A","a_correct":true,"b_correct":false,'
                          '"a_complete":true,"b_complete":false}')
        assert v["winner"] == "A" and v["a_correct"] is True and v["b_complete"] is False

    def test_parses_through_tui_chrome_and_fences(self):
        raw = ('> build · deepseek\x1b[0m\n```json\n{"winner":"B","a_correct":false,'
               '"b_correct":true,"a_complete":false,"b_complete":true}\n```\ndone')
        assert parse_verdict(raw)["winner"] == "B"

    def test_unparseable_reply_returns_none_rather_than_guessing(self):
        assert parse_verdict("the second one seems better") is None
        assert parse_verdict("") is None

    def test_an_invalid_winner_is_rejected(self):
        assert parse_verdict('{"winner":"C","a_correct":true,"b_correct":true,'
                             '"a_complete":true,"b_complete":true}') is None


class TestBlindingAndUnmapping:
    """The load-bearing wiring: shuffle, then map the verdict back correctly."""

    def _judge_always_picks(self, letter):
        def _j(model, prompt, cwd=None):
            return json.dumps({"winner": letter, "a_correct": True, "b_correct": True,
                               "a_complete": True, "b_complete": True})
        return _j

    def test_position_is_randomised_across_tasks(self, tmp_path):
        pairs = [_pair(task=f"t{i}") for i in range(20)]
        r = grade(pairs, models=("opencode-go/x",), judge=self._judge_always_picks("A"), seed=7)
        placements = {row["terse_shown_as"] for row in r["rows"]}
        assert placements == {"A", "B"}, "arms were never swapped — position bias uncontrolled"

    def test_verdict_is_unmapped_to_the_right_arm(self, tmp_path):
        """A judge that always says 'A' must produce wins for whichever arm was
        placed at A, not always for the same arm."""
        pairs = [_pair(task=f"t{i}") for i in range(20)]
        r = grade(pairs, models=("opencode-go/x",), judge=self._judge_always_picks("A"), seed=7)
        for row in r["rows"]:
            expected = "terse" if row["terse_shown_as"] == "A" else "baseline"
            assert row["winner_arm"] == expected

    def test_the_same_seed_reproduces_the_same_placement(self):
        pairs = [_pair(task=f"t{i}") for i in range(10)]
        j = self._judge_always_picks("A")
        a = grade(pairs, models=("opencode-go/x",), judge=j, seed=3)
        b = grade(pairs, models=("opencode-go/x",), judge=j, seed=3)
        assert [x["terse_shown_as"] for x in a["rows"]] == [x["terse_shown_as"] for x in b["rows"]]

    def test_position_bias_is_reported_not_assumed_away(self):
        """A judge that always picks the first slot should be visible as such."""
        pairs = [_pair(task=f"t{i}") for i in range(20)]
        r = grade(pairs, models=("opencode-go/x",), judge=self._judge_always_picks("A"), seed=7)
        assert r["position_bias"]["A"] == 20 and r["position_bias"]["B"] == 0


class TestJudgeIndependence:
    def test_refuses_a_non_opencode_go_judge(self):
        """Same rule as eval labelling: the grader may not be a component of the
        system under test, and zen models are never used here."""
        with pytest.raises(SystemExit, match="opencode-go"):
            grade([_pair()], models=("llama-cpp/qwen35b",), judge=lambda *a, **k: "{}")

    def test_refuses_the_local_model_under_test(self):
        with pytest.raises(SystemExit, match="opencode-go"):
            grade([_pair()], models=("gemma-4-e4b-it-4bit",), judge=lambda *a, **k: "{}")


class TestTallyAndDecision:
    def test_tally_counts_correctness_per_arm(self):
        rows = [{"baseline_correct": True, "terse_correct": True,
                 "baseline_complete": True, "terse_complete": True, "winner_arm": "terse"},
                {"baseline_correct": False, "terse_correct": True,
                 "baseline_complete": False, "terse_complete": True, "winner_arm": "terse"}]
        t = tally(rows)
        assert t["baseline_correct_rate"] == 0.5 and t["terse_correct_rate"] == 1.0
        assert t["terse_wins"] == 2

    def test_pre_registered_rule_kills_on_a_correctness_regression(self):
        """The brief: 'kill it if the A/B shows quality regression'."""
        v = decide({"baseline_correct_rate": 0.90, "terse_correct_rate": 0.70,
                    "baseline_complete_rate": 0.9, "terse_complete_rate": 0.9})
        assert v["keep"] is False
        assert "correctness" in " ".join(v["reasons"]).lower()

    def test_keeps_when_quality_holds(self):
        v = decide({"baseline_correct_rate": 0.80, "terse_correct_rate": 0.83,
                    "baseline_complete_rate": 0.7, "terse_complete_rate": 0.95})
        assert v["keep"] is True and v["reasons"] == []

    def test_a_regression_within_tolerance_does_not_kill(self):
        v = decide({"baseline_correct_rate": 0.80,
                    "terse_correct_rate": 0.80 - QUALITY_TOLERANCE,
                    "baseline_complete_rate": 0.9, "terse_complete_rate": 0.9})
        assert v["keep"] is True

    def test_completeness_regression_also_kills(self):
        v = decide({"baseline_correct_rate": 0.8, "terse_correct_rate": 0.8,
                    "baseline_complete_rate": 0.95, "terse_complete_rate": 0.60})
        assert v["keep"] is False
        assert "complete" in " ".join(v["reasons"]).lower()


class TestAskingForMissingContextIsNotAFailure:
    """A2b was INVALIDATED by this: 64% of its pool named files the model could
    not see, the baseline correctly asked for them on 31/53 tasks, and the
    rubric graded all 31 incorrect — so the gate rewarded CONFABULATION and
    reported a +0.245 'improvement' that was nothing of the kind."""

    def test_rubric_credits_asking_for_genuinely_missing_context(self):
        p = build_prompt("t", "a", "b").lower()
        assert "cannot be" in p or "not provided" in p or "missing" in p, \
            "rubric does not tell the judge how to score an unanswerable request"

    def test_rubric_penalises_inventing_content(self):
        p = build_prompt("t", "a", "b").lower()
        assert "invent" in p or "fabricat" in p or "made up" in p, \
            "rubric does not warn against fabricated file content"
