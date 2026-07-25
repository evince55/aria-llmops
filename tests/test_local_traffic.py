"""Tests for the local-route traffic generator (unblocks A1's measurement / A2).

No model is loaded: the router is injected as a fake, so these pin the wiring
and the accounting rather than the inference.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.local_traffic import TASKS, TERSE_CLAUSE, run  # noqa: E402


class _Router:
    """Records the prompts it was handed and returns a canned usage block."""

    def __init__(self, output="ok", in_t=20, out_t=300, executed=True, boom=False):
        self.seen, self._o, self._i, self._t = [], output, in_t, out_t
        self._executed, self._boom = executed, boom

    def run_task(self, task, max_tokens=800, log_usage=False):
        self.seen.append(task)
        if self._boom:
            raise RuntimeError("local endpoint down")
        return {"model": "llama-cpp/qwen35b", "executed": self._executed, "output": self._o,
                "usage": {"input_tokens": self._i, "output_tokens": self._t}}


class TestArms:
    def test_baseline_sends_the_task_unmodified(self, tmp_path):
        r = _Router()
        run("baseline", tmp_path / "l.jsonl", limit=2, router=r)
        assert r.seen[0] == TASKS[0]

    def test_terse_prepends_the_brevity_clause(self, tmp_path):
        r = _Router()
        run("terse", tmp_path / "l.jsonl", limit=2, router=r)
        assert r.seen[0].startswith(TERSE_CLAUSE)
        assert r.seen[0].endswith(TASKS[0])

    def test_the_clause_is_the_only_difference_between_arms(self, tmp_path):
        """An A/B where the arms differ in more than the treatment is not an A/B."""
        a, b = _Router(), _Router()
        run("baseline", tmp_path / "a.jsonl", limit=3, router=a)
        run("terse", tmp_path / "b.jsonl", limit=3, router=b)
        assert [p.replace(TERSE_CLAUSE, "") for p in b.seen] == a.seen


class TestAccounting:
    def test_sums_tokens_and_computes_the_ratio(self, tmp_path):
        s = run("baseline", tmp_path / "l.jsonl", limit=2, router=_Router(in_t=100, out_t=300))
        assert s["input_tokens"] == 200 and s["output_tokens"] == 600
        assert s["output_ratio_excl_cache"] == pytest.approx(0.75)
        assert s["mean_output_tokens"] == 300

    def test_counts_empty_outputs_separately(self, tmp_path):
        """Tokens spent with no answer produced — the reasoning-preamble trap.
        It must not read as a clean run."""
        s = run("baseline", tmp_path / "l.jsonl", limit=2, router=_Router(output="   "))
        assert s["empty_outputs"] == 2
        assert s["output_tokens"] == 600  # still billed

    def test_a_failing_call_is_counted_not_fatal(self, tmp_path):
        s = run("baseline", tmp_path / "l.jsonl", limit=3, router=_Router(boom=True))
        assert s["n"] == 0 and s["failed"] == 3

    def test_empty_run_does_not_divide_by_zero(self, tmp_path):
        s = run("baseline", tmp_path / "l.jsonl", limit=1, router=_Router(boom=True))
        assert s["output_ratio_excl_cache"] == 0.0 and s["mean_output_tokens"] == 0

    def test_limit_bounds_the_run(self, tmp_path):
        r = _Router()
        assert run("baseline", tmp_path / "l.jsonl", limit=5, router=r)["n"] == 5
        assert len(r.seen) == 5


class TestTaskSet:
    def test_tasks_are_unique(self):
        assert len(set(TASKS)) == len(TASKS)

    def test_tasks_are_local_route_shaped(self):
        """SIMPLE/MODERATE work — the tiers TIER_PREFERENCE actually sends local."""
        assert len(TASKS) >= 20
        assert all(20 <= len(t) <= 300 for t in TASKS)
