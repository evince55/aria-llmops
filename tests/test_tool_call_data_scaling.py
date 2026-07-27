"""Guards on the widened training vocabulary.

The flat lists this replaces saturated at 1,540 unique examples, which made the
paper's 10k-100k range unreachable rather than merely untested. Composing the
vocabulary lifts the ceiling — and a vocabulary large enough to be useful is
also large enough to collide with an eval set by accident, so the disjointness
that used to be maintainable by eye is now asserted.
"""
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.tool_call_data import (  # noqa: E402
    _CONTENTS, _PATHS, _PATTERNS, _TARGETS, generate, overlap_with_held_out,
    phrasing_overlap, to_mlx,
)
from evals.tool_calls import (  # noqa: E402
    FRESH_TASKS, HARD_TASKS, REGRESSION_TASKS, TASKS, TOOLS, validate,
)

SIZES = (460, 1000, 2500, 5000, 10000)


def _eval_values():
    out = set()
    for rows in (TASKS, HARD_TASKS, FRESH_TASKS, REGRESSION_TASKS):
        out |= {v for t in rows for v in t["args"].values() if isinstance(v, str)}
    return out


class TestVocabularyReachesThePapersRange:
    def test_the_generator_is_no_longer_capped_below_the_target(self):
        # The whole point: the old vocabulary returned 1540 for any larger ask.
        assert len(generate(10000, seed=1)) == 10000

    @pytest.mark.parametrize("n", SIZES)
    def test_every_curve_size_is_produced_exactly(self, n):
        assert len(generate(n, seed=1)) == n

    @pytest.mark.parametrize("n", SIZES)
    def test_every_curve_size_stays_tool_balanced(self, n):
        counts = Counter(e["tool"] for e in generate(n, seed=1))
        assert set(counts) == set(TOOLS)
        assert len(set(counts.values())) == 1, counts


class TestWidenedVocabularyStaysQuarantined:
    def test_no_vocabulary_item_appears_in_any_eval_set(self):
        banned = _eval_values()
        for name, vocab in (("paths", _PATHS), ("patterns", _PATTERNS),
                            ("targets", _TARGETS), ("contents", _CONTENTS)):
            assert not (set(vocab) & banned), f"{name} leaked into an eval set"

    @pytest.mark.parametrize("n", SIZES)
    def test_no_generated_task_is_an_eval_task(self, n):
        assert overlap_with_held_out(generate(n, seed=1)) == []

    def test_generated_tasks_never_collide_with_the_fresh_slice(self):
        tasks = {e["task"] for e in generate(10000, seed=1)}
        assert not (tasks & {t["task"] for t in FRESH_TASKS})

    def test_phrasings_still_do_not_reach_the_held_out_sets(self):
        # Finding 13: this passed exact-match quarantine while leaking.
        for rows in (TASKS, HARD_TASKS, FRESH_TASKS):
            assert phrasing_overlap(rows) == []


class TestGeneratedDataIsWellFormed:
    def test_generated_examples_match_the_declared_tool_surface(self):
        assert validate(generate(2500, seed=1)) == []

    def test_the_boolean_stays_balanced_at_scale(self):
        # A skewed verbose/quiet split would teach a prior, not an inference.
        vals = [e["args"]["verbose"] for e in generate(10000, seed=1)
                if e["tool"] == "run_tests"]
        assert abs(vals.count(True) - vals.count(False)) / len(vals) < 0.1

    def test_a_size_is_reproducible_from_its_seed(self):
        # The curve compares sizes; an irreproducible set makes that meaningless.
        a = [e["task"] for e in generate(1000, seed=3)]
        b = [e["task"] for e in generate(1000, seed=3)]
        assert a == b

    def test_train_rows_carry_the_eval_prompt_verbatim(self):
        # Train/serve parity — the rule this project learned deploying round 1.
        from evals.tool_calls import build_prompt
        ex = generate(460, seed=1)
        rows = to_mlx(ex)
        assert rows[0]["messages"][0]["content"] == build_prompt(ex[0]["task"])
