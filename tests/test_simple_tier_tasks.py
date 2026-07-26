"""Tests for A2b's SIMPLE-tier pool."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.simple_tier_tasks import CANDIDATES, select_simple  # noqa: E402


class TestSelectSimple:
    def test_keeps_only_router_classified_simple(self):
        """Membership is the ROUTER's decision — a hand-labelled pool would test
        a rule the router cannot actually apply in production."""
        def classify(t):
            return ("SIMPLE" if "typo" in t else "MODERATE"), True
        got = select_simple(classify, candidates=("fix a typo", "build a feature"))
        assert got == ["fix a typo"]

    def test_limit_bounds_the_pool(self):
        got = select_simple(lambda t: ("SIMPLE", True), candidates=CANDIDATES, limit=5)
        assert len(got) == 5

    def test_returns_empty_rather_than_raising_when_nothing_qualifies(self):
        assert select_simple(lambda t: ("COMPLEX", True), candidates=("x",)) == []


class TestPool:
    def test_candidates_are_unique(self):
        assert len(set(CANDIDATES)) == len(CANDIDATES)

    def test_pool_is_large_enough_to_improve_on_a2s_resolution(self):
        """A2's gate had n=24, so its 0.05 tolerance was 1.2 tasks. This pool
        exists to roughly double that resolution."""
        assert len(CANDIDATES) >= 45

    def test_candidates_are_task_shaped(self):
        assert all(25 <= len(t) <= 200 for t in CANDIDATES)


class TestUnanswerableWithoutContext:
    """The second half of A2b's invalidation: 64% of the pool named files the
    model cannot see, so the tasks were not answerable by a bare completion
    endpoint at all. A pool built for this harness must be checkable for that."""

    def test_flags_a_task_that_names_an_unseen_file(self):
        from evals.simple_tier_tasks import needs_file_context
        assert needs_file_context("Fix the typo in api/client.py.") is True
        assert needs_file_context("Update the README installation section.") is True

    def test_self_contained_tasks_are_not_flagged(self):
        from evals.simple_tier_tasks import needs_file_context
        assert needs_file_context("Write a function that clamps a number between min and max.") is False

    def test_the_current_pool_is_measurably_context_dependent(self):
        """Records the defect rather than hiding it: this pool CANNOT be graded
        by a file-blind harness until context is supplied."""
        from evals.simple_tier_tasks import CANDIDATES, needs_file_context
        share = sum(needs_file_context(t) for t in CANDIDATES) / len(CANDIDATES)
        assert share > 0.3, "if this drops, re-check whether A2b became runnable"
