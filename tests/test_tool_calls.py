"""Tests for S10's agentic subtask — tool-call emission.

The verifier is deterministic on purpose. A2b was voided because an LLM judge
graded a task its harness could not perform and turned an A/B into a
confabulation contest; a structural comparison against a known-correct call
cannot be flattered.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.tool_calls import (  # noqa: E402
    HARD_TASKS, TASKS, TOOLS, build_prompt, grade, native_tool_schema,
    parse_call, parse_native_call, score, validate,
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


class TestNativeToolSchema:
    """The native arm must present the SAME surface, or the comparison is rigged."""

    def test_schema_declares_exactly_the_prose_tools(self):
        assert {f["function"]["name"] for f in native_tool_schema()} == set(TOOLS)

    def test_each_tool_declares_exactly_its_prose_arguments(self):
        for f in native_tool_schema():
            fn = f["function"]
            props = fn["parameters"]["properties"]
            assert set(props) == set(TOOLS[fn["name"]]), fn["name"]

    def test_argument_types_match_the_prose_surface(self):
        want = {"str": "string", "bool": "boolean"}
        for f in native_tool_schema():
            fn = f["function"]
            for arg, typ in TOOLS[fn["name"]].items():
                assert fn["parameters"]["properties"][arg]["type"] == want[typ]

    def test_every_argument_is_required(self):
        # An optional argument would let a model omit `verbose` and still pass.
        for f in native_tool_schema():
            fn = f["function"]
            assert set(fn["parameters"]["required"]) == set(TOOLS[fn["name"]])


class TestParseNativeCall:
    def _msg(self, name, args):
        return {"tool_calls": [{"type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)}}]}

    def test_lifts_a_native_call_into_the_common_shape(self):
        got = parse_native_call(self._msg("read_file", {"path": "a.py"}))
        assert got == {"tool": "read_file", "args": {"path": "a.py"}}

    def test_grades_identically_to_the_prose_arm(self):
        # Same grade() must accept both arms, or the two are not comparable.
        native = parse_native_call(self._msg("run_tests", {"target": "api", "verbose": False}))
        assert grade(json.dumps(native), "run_tests", {"target": "api", "verbose": False})["exact"]

    def test_arguments_may_arrive_already_decoded(self):
        msg = {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.py"}}}]}
        assert parse_native_call(msg)["args"] == {"path": "a.py"}

    def test_no_tool_call_is_a_failure_not_a_guess(self):
        assert parse_native_call({"content": "I would read src/auth.py"}) is None

    def test_malformed_arguments_json_is_a_failure(self):
        msg = {"tool_calls": [{"function": {"name": "read_file", "arguments": "{path:"}}]}
        assert parse_native_call(msg) is None

    def test_a_call_without_a_name_is_not_a_call(self):
        assert parse_native_call({"tool_calls": [{"function": {"arguments": "{}"}}]}) is None

    def test_takes_the_first_call_when_several_are_emitted(self):
        msg = {"tool_calls": [
            {"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
            {"function": {"name": "search", "arguments": '{"pattern": "x", "glob": "*.py"}'}}]}
        assert parse_native_call(msg)["tool"] == "read_file"


class TestHardTasks:
    def test_the_adversarial_set_is_gradable(self):
        assert validate(HARD_TASKS) == []

    def test_it_is_disjoint_from_the_standard_set(self):
        assert not ({t["task"] for t in HARD_TASKS} & {t["task"] for t in TASKS})

    def test_it_covers_every_tool(self):
        assert {t["tool"] for t in HARD_TASKS} == set(TOOLS)

    def test_it_does_not_reuse_standard_paths_or_targets(self):
        def vals(rows):
            return {v for t in rows for v in t["args"].values() if isinstance(v, str)}
        assert not (vals(HARD_TASKS) & vals(TASKS))


class TestParseCallWithReasoningPreamble:
    """A greedy `\\{.*\\}` swallowed everything between the FIRST brace and the LAST.

    Arm C (a reasoning model) emitted a perfectly correct call after a </think>
    block and was graded parse_rate 0.00 across the board — the harness, not the
    model. Any model that reasons in prose before answering hits this, and the
    brace that starts the greedy match is usually the schema echoed back in the
    model's own reasoning.
    """

    REASONED = (
        'Thinking Process:\n'
        '1. Constraint: reply with ONE tool call as `{"tool": "<name>", "args": {...}}`.\n'
        '2. `read_file(path: str)` reads a file, so it is the right tool.\n'
        '</think>\n\n'
        '{"tool": "read_file", "args": {"path": "vendor/lib.rs"}}'
    )

    def test_finds_the_call_after_a_reasoning_preamble(self):
        assert parse_call(self.REASONED) == {
            "tool": "read_file", "args": {"path": "vendor/lib.rs"}}

    def test_grades_a_reasoned_reply_as_correct(self):
        g = grade(self.REASONED, "read_file", {"path": "vendor/lib.rs"})
        assert g["parsed"] and g["exact"]

    def test_the_last_valid_call_wins_over_an_earlier_one(self):
        # Models restate a draft then correct it; the final answer is the answer.
        raw = ('{"tool": "search", "args": {"pattern": "x", "glob": "*.py"}}\n'
               'On reflection:\n{"tool": "read_file", "args": {"path": "a.py"}}')
        assert parse_call(raw)["tool"] == "read_file"

    def test_nested_objects_are_not_truncated_at_the_first_brace(self):
        raw = 'preamble { not json\n{"tool": "run_tests", "args": {"target": "api", "verbose": false}}'
        assert parse_call(raw) == {
            "tool": "run_tests", "args": {"target": "api", "verbose": False}}

    def test_a_reply_with_only_prose_braces_still_fails(self):
        assert parse_call("I would call read_file(path) { like so }") is None

    def test_braces_inside_string_values_do_not_break_the_scan(self):
        raw = '{"tool": "write_file", "args": {"path": "a.txt", "content": "a { b } c"}}'
        assert parse_call(raw)["args"]["content"] == "a { b } c"
