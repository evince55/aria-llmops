"""Tests for A2b's context-supplying harness.

The one that matters is `test_every_task_is_answerable_from_its_file`: A2b was
invalidated because tasks named files the model never saw, so "please provide
the file" was the correct answer and the grader punished it. If a fixture drifts
away from its task, the round becomes invalid the same way — silently.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.context_fixtures import (  # noqa: E402
    FIXTURES, TASKS, render, unanswerable,
)


class TestAnswerability:
    def test_every_task_is_answerable_from_its_file(self):
        """THE invariant. A task whose target is absent from its file cannot be
        done from what the model is shown — which is exactly what voided A2b."""
        assert unanswerable() == []

    def test_a_drifted_fixture_is_caught(self):
        bad = unanswerable(
            tasks=[{"task": "fix the typo 'recieve'", "file": "f.py",
                    "must_contain": ("recieve",)}],
            fixtures={"f.py": "def clean(): pass"})
        assert bad and "recieve" in bad[0][1]

    def test_a_missing_fixture_is_caught(self):
        bad = unanswerable(
            tasks=[{"task": "t", "file": "nope.py", "must_contain": ()}],
            fixtures={})
        assert bad and "no fixture" in bad[0][1]

    def test_every_task_names_a_real_fixture(self):
        assert all(e["file"] in FIXTURES for e in TASKS)


class TestRendering:
    def test_render_includes_the_file_contents(self):
        out = render(TASKS[0])
        assert FIXTURES[TASKS[0]["file"]].split("\n")[0] in out

    def test_render_includes_the_path_and_the_task(self):
        e = TASKS[0]
        out = render(e)
        assert e["file"] in out and e["task"] in out

    def test_render_puts_the_task_after_the_file(self):
        """Instruction last, so a terse arm's clause is not buried mid-prompt."""
        e = TASKS[0]
        out = render(e)
        assert out.index(e["file"]) < out.index(e["task"])


class TestPool:
    def test_tasks_are_unique(self):
        assert len({e["task"] for e in TASKS}) == len(TASKS)

    def test_pool_beats_a2s_resolution(self):
        """A2 ran n=24, so its 0.05 tolerance was 1.2 tasks."""
        assert len(TASKS) >= 25

    def test_fixtures_are_small_enough_to_be_about_the_task(self):
        """This tests brevity, not long-context handling."""
        assert all(len(c) < 900 for c in FIXTURES.values())
