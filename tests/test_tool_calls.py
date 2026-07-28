"""Tests for S10's agentic subtask — tool-call emission.

The verifier is deterministic on purpose. A2b was voided because an LLM judge
graded a task its harness could not perform and turned an A/B into a
confabulation contest; a structural comparison against a known-correct call
cannot be flattered.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.tool_calls import (  # noqa: E402
    FRESH_TASKS, HARD_TASKS, REGRESSION_TASKS, TASKS, TOOLS, WIDE_TASKS,
    build_prompt, grade, is_deterministic, native_tool_schema, operating_point,
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
                     "exact": False, "truncated": False, "got": None,
                     "output_tokens": None}

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


class TestWideAdversarialSet:
    """Guards the widened set against every contamination this project has hit.

    Rules fixed in docs/research/2026-07-26-wide-set-preregistration.md before a
    single task was authored, because the arms' row-level failures were already
    known and freehand authoring would have drifted toward discriminating shapes.
    """

    def _values(self, rows):
        return {v for t in rows for v in t["args"].values() if isinstance(v, str)}

    def test_fresh_slice_is_gradable(self):
        assert validate(FRESH_TASKS) == []

    def test_regression_slice_is_gradable(self):
        assert validate(REGRESSION_TASKS) == []

    def test_fresh_covers_every_tool_evenly(self):
        counts = Counter(t["tool"] for t in FRESH_TASKS)
        assert set(counts) == set(TOOLS) and set(counts.values()) == {12}

    def test_fresh_is_disjoint_from_both_existing_sets(self):
        existing = {t["task"] for t in TASKS} | {t["task"] for t in HARD_TASKS}
        assert not ({t["task"] for t in FRESH_TASKS} & existing)

    def test_fresh_reuses_no_argument_value_from_the_eval_sets(self):
        # A memorised filename must not be answerable.
        assert not (self._values(FRESH_TASKS)
                    & (self._values(TASKS) | self._values(HARD_TASKS)))

    def test_fresh_reuses_no_training_vocabulary(self):
        from evals.tool_call_data import _CONTENTS, _GLOBS, _PATHS, _PATTERNS, _TARGETS
        training = set(_PATHS) | set(_PATTERNS) | set(_GLOBS.values()) | set(_TARGETS) | set(_CONTENTS)
        leaked = self._values(FRESH_TASKS) & training
        # Globs are a closed vocabulary the schema implies; paths/patterns/
        # contents/targets are not, and those are what memorisation would exploit.
        assert not (leaked - set(_GLOBS.values())), leaked

    def test_no_held_out_phrasing_is_reachable_from_a_training_template(self):
        # Finding 13: this passed exact-match quarantine while leaking.
        from evals.tool_call_data import phrasing_overlap
        assert phrasing_overlap(FRESH_TASKS) == []

    def test_fresh_patterns_carry_no_regex_metacharacters(self):
        # Two contested rows in the n=13 set are arguments about an
        # underspecified schema, not model errors. Do not multiply that by four.
        meta = set("()[]{}*+?^$|\\!")
        for t in FRESH_TASKS:
            if t["tool"] == "search":
                assert not (set(t["args"]["pattern"]) & meta), t["task"]

    def test_fresh_globs_use_one_consistent_style(self):
        for t in FRESH_TASKS:
            if t["tool"] == "search":
                assert t["args"]["glob"].startswith("*."), t["task"]

    def test_fresh_balances_the_boolean_it_must_infer(self):
        vals = [t["args"]["verbose"] for t in FRESH_TASKS if t["tool"] == "run_tests"]
        assert vals.count(True) == vals.count(False)

    def test_regression_is_excluded_from_the_generalisation_instrument(self):
        # It is fitted to observed failures by construction.
        assert not ({t["task"] for t in WIDE_TASKS} & {t["task"] for t in REGRESSION_TASKS})

    def test_wide_is_exactly_the_original_plus_fresh(self):
        assert len(WIDE_TASKS) == len(HARD_TASKS) + len(FRESH_TASKS) == 61


class TestOperatingPoint:
    """A sampling configuration is part of the measurement, not a default to inherit.

    Finding 19: every served run forced temperature 0 on a model benchmarked at
    1.0. The same was true of the local arms in the opposite direction — Gemma-4
    ships `temperature: 1.0, top_p: 0.95, top_k: 64` and every E2B number was
    taken greedy. An operating point has to be declared, recorded, and justified
    per deployment.
    """

    def test_greedy_is_a_named_point_not_an_absence(self):
        assert operating_point("greedy") == {"temp": 0.0, "top_p": 0.0, "top_k": 0}

    def test_a_card_spec_carries_all_three_knobs(self):
        p = operating_point("card", temp=1.0, top_p=0.95, top_k=64)
        assert p == {"temp": 1.0, "top_p": 0.95, "top_k": 64}

    def test_an_unknown_name_is_refused_rather_than_defaulted(self):
        # Silently defaulting is how every arm ended up greedy without anyone
        # deciding it should be.
        with pytest.raises(ValueError):
            operating_point("whatever")

    def test_card_requires_its_values_to_be_supplied(self):
        with pytest.raises(ValueError):
            operating_point("card")

    def test_a_deterministic_point_is_flagged_as_such(self):
        assert is_deterministic(operating_point("greedy"))
        assert not is_deterministic(operating_point("card", temp=1.0, top_p=0.95, top_k=64))

    def test_temperature_zero_is_deterministic_whatever_the_other_knobs_say(self):
        assert is_deterministic({"temp": 0.0, "top_p": 0.95, "top_k": 64})


class TestOutputLength:
    """Output length is a measurement, not a diagnostic detail.

    Finding 17 was a reasoning model that never terminated. The question of
    whether fine-tuning suppresses a reasoning preamble is answerable only if
    the harness records how much the model actually emitted — and it did not.
    """

    def test_a_graded_row_records_what_the_model_emitted(self):
        g = grade('{"tool": "read_file", "args": {"path": "a.py"}}',
                  "read_file", {"path": "a.py"}, output_tokens=42)
        assert g["output_tokens"] == 42

    def test_length_is_recorded_even_when_the_answer_is_wrong(self):
        # A verbose wrong answer and a terse wrong answer are different failures.
        g = grade("no call here", "read_file", {"path": "a.py"}, output_tokens=900)
        assert g["exact"] is False and g["output_tokens"] == 900

    def test_an_unmeasured_row_reports_none_rather_than_zero(self):
        # Zero would silently drag a mean toward "terse".
        assert grade("{}", "read_file", {"path": "a.py"})["output_tokens"] is None

    def test_score_reports_mean_output_length(self):
        rows = [grade("{}", "x", {}, output_tokens=n) for n in (100, 200, 300)]
        assert score(rows)["mean_output_tokens"] == 200

    def test_mean_ignores_unmeasured_rows_instead_of_counting_them_as_zero(self):
        rows = [grade("{}", "x", {}, output_tokens=100), grade("{}", "x", {})]
        assert score(rows)["mean_output_tokens"] == 100

    def test_a_run_with_no_lengths_reports_none_not_zero(self):
        assert score([grade("{}", "x", {})])["mean_output_tokens"] is None
