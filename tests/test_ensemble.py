"""Tests for the S10 ensemble — written before the ensemble was run.

The rules are pre-registered (docs/research/2026-07-26-s10-ensemble-preregistration.md)
because both arms' row-level failures were already known. These tests pin the
rules' BEHAVIOUR so the rules cannot quietly drift toward the data once numbers
come back.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.ensemble import agree_or_escalate, cascade_score, majority, vote_score  # noqa: E402
from evals.run_ensemble import combine  # noqa: E402

READ_A = {"tool": "read_file", "args": {"path": "a.py"}}
READ_B = {"tool": "read_file", "args": {"path": "b.py"}}
SEARCH = {"tool": "search", "args": {"pattern": "x", "glob": "*.py"}}


class TestAgreeOrEscalate:
    def test_identical_calls_are_accepted(self):
        r = agree_or_escalate([READ_A, dict(READ_A)])
        assert r["accepted"] is True and r["call"] == READ_A

    def test_different_args_escalate(self):
        assert agree_or_escalate([READ_A, READ_B])["accepted"] is False

    def test_different_tools_escalate(self):
        assert agree_or_escalate([READ_A, SEARCH])["accepted"] is False

    def test_a_missing_call_escalates_rather_than_deferring(self):
        # One arm failing to answer is not licence to trust the other alone.
        assert agree_or_escalate([READ_A, None])["accepted"] is False
        assert agree_or_escalate([None, None])["accepted"] is False

    def test_an_escalated_row_carries_no_call(self):
        assert agree_or_escalate([READ_A, SEARCH])["call"] is None

    def test_argument_order_does_not_count_as_disagreement(self):
        a = {"tool": "search", "args": {"pattern": "x", "glob": "*.py"}}
        b = {"tool": "search", "args": {"glob": "*.py", "pattern": "x"}}
        assert agree_or_escalate([a, b])["accepted"] is True


class TestMajority:
    def test_two_of_three_wins(self):
        assert majority([READ_A, dict(READ_A), SEARCH]) == READ_A

    def test_three_way_split_abstains(self):
        assert majority([READ_A, READ_B, SEARCH]) is None

    def test_unanimous_wins(self):
        assert majority([READ_A, dict(READ_A), dict(READ_A)]) == READ_A

    def test_missing_calls_do_not_form_a_majority(self):
        # Two arms failing to answer must not agree on "no call".
        assert majority([READ_A, None, None]) is None

    def test_a_majority_survives_one_missing_arm(self):
        assert majority([READ_A, dict(READ_A), None]) == READ_A


class TestCascadeScore:
    def _rows(self):
        # 3 covered and correct, 1 covered and wrong, 2 escalated.
        return [
            {"accepted": True, "call": READ_A, "truth": READ_A},
            {"accepted": True, "call": READ_A, "truth": READ_A},
            {"accepted": True, "call": READ_A, "truth": READ_A},
            {"accepted": True, "call": READ_B, "truth": READ_A},
            {"accepted": False, "call": None, "truth": READ_A},
            {"accepted": False, "call": None, "truth": SEARCH},
        ]

    def test_coverage_is_the_accepted_share(self):
        assert cascade_score(self._rows())["coverage"] == 4 / 6

    def test_precision_is_measured_only_on_covered_rows(self):
        assert cascade_score(self._rows())["precision_on_covered"] == 3 / 4

    def test_escalation_rate_complements_coverage(self):
        s = cascade_score(self._rows())
        assert s["escalation_rate"] == 1 - s["coverage"]

    def test_end_to_end_accuracy_counts_escalations_as_unanswered(self):
        # A cascade that abstains has not answered; it must not be credited.
        assert cascade_score(self._rows())["accuracy_if_unanswered_is_wrong"] == 3 / 6

    def test_zero_coverage_does_not_divide_by_zero(self):
        rows = [{"accepted": False, "call": None, "truth": READ_A}]
        s = cascade_score(rows)
        assert s["coverage"] == 0.0 and s["precision_on_covered"] is None


class TestVoteScore:
    def test_accuracy_over_all_rows(self):
        rows = [{"call": READ_A, "truth": READ_A}, {"call": None, "truth": READ_A},
                {"call": READ_B, "truth": READ_A}, {"call": SEARCH, "truth": SEARCH}]
        s = vote_score(rows)
        assert s["accuracy"] == 2 / 4

    def test_abstentions_are_reported_separately_from_wrong_answers(self):
        rows = [{"call": None, "truth": READ_A}, {"call": READ_B, "truth": READ_A}]
        s = vote_score(rows)
        assert s["abstain_rate"] == 0.5 and s["accuracy"] == 0.0


class TestCombineJoinsByTask:
    """Rows are joined by task text, never by index.

    Arms are run at different times, on different stacks, sometimes over
    different subsets. An index join would silently pair arm A's row 3 with arm
    B's row 3 after one arm dropped a row — and the ensemble would be scored
    over a row set that matches neither member while looking like a clean gain.
    """

    def _log(self, tmp_path, name, pairs):
        p = tmp_path / name
        p.write_text(json.dumps({"rows": [{"task": t, "got": g} for t, g in pairs]}))
        return str(p)

    def test_reordered_arms_still_pair_correctly(self, tmp_path):
        tasks = [{"task": "t1", "tool": "read_file", "args": {"path": "a.py"}},
                 {"task": "t2", "tool": "read_file", "args": {"path": "b.py"}}]
        call1 = {"tool": "read_file", "args": {"path": "a.py"}}
        call2 = {"tool": "read_file", "args": {"path": "b.py"}}
        a = self._log(tmp_path, "a.json", [("t1", call1), ("t2", call2)])
        b = self._log(tmp_path, "b.json", [("t2", call2), ("t1", call1)])  # reversed
        res = combine([a, b], tasks)
        assert res["rule_a_agree_or_escalate"]["precision_on_covered"] == 1.0

    def test_a_missing_task_is_a_hard_error(self, tmp_path):
        tasks = [{"task": "t1", "tool": "read_file", "args": {"path": "a.py"}},
                 {"task": "t2", "tool": "read_file", "args": {"path": "b.py"}}]
        call1 = {"tool": "read_file", "args": {"path": "a.py"}}
        a = self._log(tmp_path, "a.json", [("t1", call1), ("t2", call1)])
        b = self._log(tmp_path, "b.json", [("t1", call1)])
        with pytest.raises(SystemExit):
            combine([a, b], tasks)

    def test_rule_b_is_absent_with_only_two_arms(self, tmp_path):
        tasks = [{"task": "t1", "tool": "read_file", "args": {"path": "a.py"}}]
        call1 = {"tool": "read_file", "args": {"path": "a.py"}}
        a = self._log(tmp_path, "a.json", [("t1", call1)])
        b = self._log(tmp_path, "b.json", [("t1", call1)])
        assert "rule_b_majority_vote" not in combine([a, b], tasks)


class TestRulesGeneraliseBeyondToolCalls:
    """The router's answers are label strings, not {tool, args} dicts.

    Rule A is the only round-2 claim that survived every instrument change, so
    it is the one worth transferring. Transferring it must not mean rewriting
    it — a re-implementation for a second answer type is a second thing to get
    wrong, and the two would drift.
    """

    def test_identical_labels_are_accepted(self):
        assert agree_or_escalate(["COMPLEX", "COMPLEX"])["accepted"] is True

    def test_different_labels_escalate(self):
        assert agree_or_escalate(["COMPLEX", "MODERATE"])["accepted"] is False

    def test_a_missing_label_escalates(self):
        assert agree_or_escalate(["COMPLEX", None])["accepted"] is False

    def test_majority_works_on_labels(self):
        assert majority(["COMPLEX", "COMPLEX", "SIMPLE"]) == "COMPLEX"

    def test_a_three_way_label_split_abstains(self):
        assert majority(["COMPLEX", "SIMPLE", "CRITICAL"]) is None

    def test_cascade_scores_labels(self):
        rows = [{"accepted": True, "call": "COMPLEX", "truth": "COMPLEX"},
                {"accepted": True, "call": "SIMPLE", "truth": "COMPLEX"},
                {"accepted": False, "call": None, "truth": "MODERATE"}]
        s = cascade_score(rows)
        assert s["coverage"] == 2/3 and s["precision_on_covered"] == 0.5

    def test_empty_string_is_not_an_answer(self):
        # A classifier returning "" must escalate, not agree with another "".
        assert agree_or_escalate(["", ""])["accepted"] is False
