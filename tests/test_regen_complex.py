"""Tests for the COMPLEX regeneration and the train_v3 assembly.

The defect these guard against is the one the promotion gate measured: a
COMPLEX training slice made of tasks that name both the cause and the remedy,
which judges read as MODERATE.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.assemble_train_v3 import main as assemble_main, stratified_take  # noqa: E402
from evals.regen_complex import extract_tasks, prescribes_fix  # noqa: E402


class TestPrescribesFix:
    """The generator's cheap guard: a task that hands over the remedy is the
    exact failure that cost 18 points of COMPLEX recall."""

    @pytest.mark.parametrize("task", [
        "The /api/radio endpoint has an N+1 query problem; switch to eager loading.",
        "This handler makes sequential blocking calls, convert them to a shared httpx.AsyncClient.",
        "Two clients race to write the same file; add a per-video-id asyncio lock.",
        "The retry logic is wrong here, the fix is to use exponential backoff.",
        "This should be changed to a background task queue.",
    ])
    def test_flags_prescribed_remedies(self, task):
        assert prescribes_fix(task) is True

    @pytest.mark.parametrize("task", [
        "Requests to /api/radio get dramatically slower as the library grows and I "
        "can't work out why. Profile it and fix whatever is actually causing it.",
        "Our test suite is flaky in CI but green locally. Something is leaking state "
        "between tests but I haven't found it. Track it down.",
        "Memory climbs steadily until the pod is OOM-killed. I suspect an error path "
        "isn't releasing something, but I haven't confirmed it.",
    ])
    def test_keeps_symptom_with_uncertainty(self, task):
        assert prescribes_fix(task) is False

    def test_empty_input_is_not_a_crash(self):
        assert prescribes_fix("") is False
        assert prescribes_fix(None) is False


class TestExtractTasks:
    """opencode wraps replies in TUI chrome, and models sometimes echo the
    prompt's own example array — take the LONGEST valid array, not the first."""

    def test_extracts_from_tui_chrome(self):
        raw = '> build \x1b[0m\n[{"task": "alpha"}, {"task": "beta"}]\n done'
        assert extract_tasks(raw) == ["alpha", "beta"]

    def test_prefers_longest_array_over_an_echoed_example(self):
        raw = '[{"task": "echoed example"}] then [{"task": "a"}, {"task": "b"}, {"task": "c"}]'
        assert extract_tasks(raw) == ["a", "b", "c"]

    def test_malformed_round_yields_empty_not_exception(self):
        assert extract_tasks("sorry, I cannot comply") == []
        assert extract_tasks("") == []

    def test_skips_entries_missing_a_task_string(self):
        raw = '[{"task": "ok"}, {"tier": "COMPLEX"}, {"task": ""}, {"task": 7}]'
        assert extract_tasks(raw) == ["ok"]


class TestStratifiedTake:
    def test_round_robins_across_domains(self):
        rows = ([{"domain": "a", "task": f"a{i}"} for i in range(10)] +
                [{"domain": "b", "task": f"b{i}"} for i in range(10)])
        got = stratified_take(rows, 6)
        assert len(got) == 6
        assert sum(r["domain"] == "a" for r in got) == 3

    def test_drains_gracefully_when_a_domain_runs_out(self):
        rows = [{"domain": "a", "task": "a1"}] + [{"domain": "b", "task": f"b{i}"} for i in range(5)]
        got = stratified_take(rows, 4)
        assert len(got) == 4
        assert sum(r["domain"] == "a" for r in got) == 1

    def test_never_returns_more_than_available(self):
        rows = [{"domain": "a", "task": "only"}]
        assert len(stratified_take(rows, 99)) == 1


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


class TestAssembleTrainV3:
    def _fixtures(self, tmp_path, n_fresh=10):
        base = tmp_path / "base.jsonl"
        _write(base, [{"task": f"old complex {i}", "tier": "COMPLEX", "source": "v2"} for i in range(3)] +
                     [{"task": f"mod {i}", "tier": "MODERATE", "source": "v2"} for i in range(4)] +
                     [{"task": "crit", "tier": "CRITICAL", "source": "v2"}])
        fresh = tmp_path / "fresh.jsonl"
        _write(fresh, [{"task": f"new complex {i}", "tier": "COMPLEX",
                        "domain": ["ios", "backend"][i % 2]} for i in range(n_fresh)])
        ev = tmp_path / "eval.jsonl"
        _write(ev, [{"task": "an unrelated eval row", "expected_tier": "SIMPLE"}])
        return base, fresh, ev

    def _run(self, tmp_path, base, fresh, ev):
        out, sur = tmp_path / "v3.jsonl", tmp_path / "sur.jsonl"
        rc = assemble_main(["--base", str(base), "--complex", str(fresh),
                            "--eval-set", str(ev), "--out", str(out), "--surplus", str(sur)])
        assert rc == 0
        return ([json.loads(l) for l in out.read_text().splitlines() if l],
                [json.loads(l) for l in sur.read_text().splitlines() if l])

    def test_holds_complex_count_constant(self, tmp_path):
        """The load-bearing design rule: swap text, not size, or the result is
        confounded between data quality and data volume."""
        rows, surplus = self._run(tmp_path, *self._fixtures(tmp_path))
        cx = [r for r in rows if r["tier"] == "COMPLEX"]
        assert len(cx) == 3
        assert all(r["source"] == "synthetic-v3-complex" for r in cx)
        assert len(surplus) == 7

    def test_preserves_every_other_tier_untouched(self, tmp_path):
        rows, _ = self._run(tmp_path, *self._fixtures(tmp_path))
        assert sum(r["tier"] == "MODERATE" for r in rows) == 4
        assert sum(r["tier"] == "CRITICAL" for r in rows) == 1
        assert not any("old complex" in r["task"] for r in rows)

    def test_quarantine_breach_aborts(self, tmp_path):
        """A training row that is also an eval row invalidates the measurement,
        so this must fail loudly rather than silently produce a good score."""
        base, fresh, ev = self._fixtures(tmp_path)
        _write(ev, [{"task": "New Complex 1  ", "expected_tier": "COMPLEX"}])  # case/space variant
        with pytest.raises(SystemExit, match="QUARANTINE BREACH"):
            self._run(tmp_path, base, fresh, ev)

    def test_warns_but_proceeds_when_fresh_rows_are_short(self, tmp_path, capsys):
        base, fresh, ev = self._fixtures(tmp_path, n_fresh=2)
        rows, _ = self._run(tmp_path, base, fresh, ev)
        assert sum(r["tier"] == "COMPLEX" for r in rows) == 2
        assert "size is no longer controlled" in capsys.readouterr().err
