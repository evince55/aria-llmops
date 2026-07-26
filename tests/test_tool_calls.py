"""Tests for S10's agentic subtask — tool-call emission.

The verifier is deterministic on purpose. A2b was voided because an LLM judge
graded a task its harness could not perform and turned an A/B into a
confabulation contest; a structural comparison against a known-correct call
cannot be flattered.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.tool_calls import (  # noqa: E402
    TASKS, TOOLS, build_prompt, grade, parse_call, score, validate,
)


class TestParseCall:
    def test_parses_a_plain_call(self):
        assert parse_call('{"tool":"read_file","args":{"path":"a.py"}}')["tool"] == "read_file"

    def test_parses_through_prose_and_fences(self):
        raw = 'Sure!\n```json\n{"tool":"search","args":{"pattern":"x","glob":"*.py"}}\n```\ndone'
        assert parse_call(raw)["tool"] == "search"

    def test_unparseable_returns_none_rather_than_guessing(self):
        """Coercion is how a broken model comes to look competent."""
        assert parse_call("I would read the file src/auth.py") is None
        assert parse_call("") is None

    def test_an_object_without_a_tool_key_is_not_a_call(self):
        assert parse_call('{"path": "a.py"}') is None


class TestGrade:
    def test_exact_match_passes_every_level(self):
        g = grade('{"tool":"read_file","args":{"path":"a.py"}}', "read_file", {"path": "a.py"})
        assert (g["parsed"], g["tool_ok"], g["args_ok"], g["exact"]) == (True, True, True, True)

    def test_right_tool_wrong_args_is_the_measured_failure_mode(self):
        """The probe's gap: 9/10 tools right, 7/10 args right."""
        g = grade('{"tool":"read_file","args":{"path":"src/auth.py"}}', "read_file", {"path": "a.py"})
        assert g["tool_ok"] is True and g["args_ok"] is False and g["exact"] is False

    def test_wrong_tool_fails_even_with_matching_args(self):
        g = grade('{"tool":"write_file","args":{"path":"a.py"}}', "read_file", {"path": "a.py"})
        assert g["tool_ok"] is False and g["exact"] is False

    def test_a_string_true_is_not_a_boolean(self):
        """Argument PRECISION is the skill under test; types are part of it."""
        g = grade('{"tool":"run_tests","args":{"target":"api","verbose":"true"}}',
                  "run_tests", {"target": "api", "verbose": True})
        assert g["args_ok"] is False

    def test_unparseable_fails_closed(self):
        g = grade("no json here", "read_file", {"path": "a.py"})
        assert g == {"parsed": False, "tool_ok": False, "args_ok": False,
                     "exact": False, "truncated": False, "got": None}

    def test_extra_argument_is_not_exact(self):
        g = grade('{"tool":"read_file","args":{"path":"a.py","mode":"r"}}',
                  "read_file", {"path": "a.py"})
        assert g["args_ok"] is False


class TestScore:
    def test_reports_the_three_levels_separately(self):
        rows = [grade('{"tool":"read_file","args":{"path":"a.py"}}', "read_file", {"path": "a.py"}),
                grade('{"tool":"read_file","args":{"path":"WRONG"}}', "read_file", {"path": "a.py"}),
                grade("garbage", "read_file", {"path": "a.py"})]
        s = score(rows)
        assert s["parse_rate"] == pytest.approx(2/3)
        assert s["tool_accuracy"] == pytest.approx(2/3)
        assert s["strict_accuracy"] == pytest.approx(1/3)

    def test_empty_does_not_divide_by_zero(self):
        assert score([])["strict_accuracy"] == 0.0


class TestTaskSetValidity:
    def test_every_task_is_gradable_against_the_declared_schema(self):
        """The A2b analogue: an expected call that does not match the tool
        surface can never be produced correctly, so the model would be graded
        against something it was never shown."""
        assert validate() == []

    def test_a_task_naming_an_unknown_tool_is_caught(self):
        bad = validate([{"task": "t", "tool": "nope", "args": {}}])
        assert bad and "unknown tool" in bad[0][1]

    def test_a_task_with_the_wrong_arg_names_is_caught(self):
        bad = validate([{"task": "t", "tool": "read_file", "args": {"file": "a.py"}}])
        assert bad and "schema" in bad[0][1]

    def test_a_bool_argument_given_as_a_string_is_caught(self):
        bad = validate([{"task": "t", "tool": "run_tests",
                         "args": {"target": "a", "verbose": "yes"}}])
        assert bad and "bool" in bad[0][1]

    def test_tasks_are_unique_and_cover_every_tool(self):
        assert len({t["task"] for t in TASKS}) == len(TASKS)
        assert {t["tool"] for t in TASKS} == set(TOOLS)

    def test_prompt_shows_the_schema_and_the_task(self):
        p = build_prompt("Read a.py")
        assert "read_file" in p and "Read a.py" in p and "strict JSON" in p


class TestTruncationIsNotFailure:
    """The fifth appearance of this trap in the project.

    A first baseline read 0.55 strict accuracy with a 35% parse-failure rate.
    It was measuring `max_tokens=300`: the model emits a reasoning preamble and
    needs ~427 tokens, so it hit the cap with an EMPTY content field. At 700 it
    finishes and parses. "Ran out of room" and "could not do it" must never be
    the same number.
    """

    def test_truncation_is_reported_separately_from_a_parse_failure(self):
        g = grade("", "read_file", {"path": "a.py"}, finish_reason="length")
        assert g["truncated"] is True and g["parsed"] is False

    def test_a_genuine_parse_failure_is_not_marked_truncated(self):
        g = grade("I would read the file.", "read_file", {"path": "a.py"}, finish_reason="stop")
        assert g["truncated"] is False and g["parsed"] is False

    def test_a_complete_answer_is_never_truncated(self):
        g = grade('{"tool":"read_file","args":{"path":"a.py"}}', "read_file",
                  {"path": "a.py"}, finish_reason="stop")
        assert g["truncated"] is False and g["exact"] is True

    def test_score_surfaces_the_truncation_rate(self):
        rows = [grade("", "read_file", {"path": "a"}, finish_reason="length"),
                grade('{"tool":"read_file","args":{"path":"a"}}', "read_file", {"path": "a"})]
        assert score(rows)["truncation_rate"] == pytest.approx(0.5)

    def test_a_run_that_truncates_heavily_is_flagged_as_unsound(self):
        """A baseline measured through a token cap is not a measurement."""
        rows = [grade("", "read_file", {"path": "a"}, finish_reason="length")] * 4
        rows += [grade('{"tool":"read_file","args":{"path":"a"}}', "read_file", {"path": "a"})]
        assert score(rows)["sound"] is False

    def test_a_clean_run_is_sound(self):
        rows = [grade('{"tool":"read_file","args":{"path":"a"}}', "read_file", {"path": "a"})] * 5
        assert score(rows)["sound"] is True
