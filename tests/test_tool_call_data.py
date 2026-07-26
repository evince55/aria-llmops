"""Tests for S10's self-supervised training data."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.tool_call_data import generate, overlap_with_held_out, to_mlx  # noqa: E402
from evals.tool_calls import TASKS as HELD_OUT, build_prompt, validate  # noqa: E402


class TestQuarantine:
    def test_no_training_task_appears_in_the_held_out_set(self):
        """Enforced in code, not intention. A training task that is also an eval
        task turns the gate into a memorisation check."""
        assert overlap_with_held_out(generate(200, seed=1)) == []

    def test_quarantine_is_case_and_whitespace_insensitive(self):
        held = [{"task": "  READ   readme.md ", "tool": "read_file", "args": {"path": "x"}}]
        ex = [{"task": "read readme.md", "tool": "read_file", "args": {"path": "x"}}]
        assert overlap_with_held_out(ex, held_out=held) == ["read readme.md"]

    def test_generated_paths_are_disjoint_from_held_out_paths(self):
        """So the model cannot score by memorising a filename."""
        train_paths = {e["args"].get("path") for e in generate(200, seed=2)
                       if e["tool"] in ("read_file", "write_file")}
        held_paths = {t["args"].get("path") for t in HELD_OUT
                      if t["tool"] in ("read_file", "write_file")}
        assert not (train_paths & held_paths)


class TestGeneratedExamplesAreValid:
    def test_every_example_matches_the_declared_tool_schema(self):
        assert validate(generate(200, seed=3)) == []

    def test_all_four_tools_are_covered(self):
        from collections import Counter
        c = Counter(e["tool"] for e in generate(200, seed=4))
        assert set(c) == {"read_file", "search", "run_tests", "write_file"}
        assert min(c.values()) > 10

    def test_tasks_are_unique(self):
        ex = generate(300, seed=5)
        assert len({e["task"] for e in ex}) == len(ex)

    def test_bools_are_real_bools_not_strings(self):
        """Argument PRECISION is the skill; a string 'true' is a wrong answer."""
        for e in generate(200, seed=6):
            if e["tool"] == "run_tests":
                assert isinstance(e["args"]["verbose"], bool)

    def test_verbose_tracks_the_phrasing(self):
        for e in generate(300, seed=7):
            if e["tool"] == "run_tests":
                # Cue words come from the TRAIN-only templates; the held-out set
                # uses different ones on purpose, which is the generalisation
                # this fine-tune is actually being asked to make.
                quiet = any(w in e["task"].lower()
                            for w in ("terse", "without detail", "silently",
                                      "keep it brief", "suppress"))
                assert e["args"]["verbose"] is not quiet

    def test_globs_are_inferred_from_the_language_word(self):
        for e in generate(300, seed=8):
            if e["tool"] == "search" and "python" in e["task"].lower():
                assert e["args"]["glob"] == "*.py"

    def test_read_path_is_extracted_from_the_phrasing(self):
        for e in generate(200, seed=9):
            if e["tool"] == "read_file":
                assert e["args"]["path"] in e["task"]


class TestMLXFormat:
    def test_train_prompt_equals_eval_prompt(self):
        """Train/serve parity — the rule this project learned deploying the
        classifier adapter."""
        ex = generate(20, seed=10)[:1]
        row = to_mlx(ex)[0]
        assert row["messages"][0]["content"] == build_prompt(ex[0]["task"])

    def test_assistant_turn_is_exactly_the_target_json(self):
        ex = [{"task": "t", "tool": "read_file", "args": {"path": "a.py"}}]
        got = json.loads(to_mlx(ex)[0]["messages"][1]["content"])
        assert got == {"tool": "read_file", "args": {"path": "a.py"}}


class TestPhrasingIsHeldOutToo:
    """Exact-task quarantine is not enough.

    The first version of the generator mirrored the held-out set's wording. No
    task overlapped, so the quarantine passed — and the tuned arm scored a
    perfect 1.00 that measured template memorisation, not capability. Phrasings
    must be disjoint as well as tasks.
    """

    def test_no_held_out_wording_is_reachable_from_a_training_template(self):
        from evals.tool_call_data import phrasing_overlap
        assert phrasing_overlap() == []

    def test_the_check_catches_a_deliberately_mirrored_template(self):
        from evals.tool_call_data import phrasing_overlap
        held = [{"task": "Fetch the contents of a.py for me.", "tool": "read_file",
                 "args": {"path": "a.py"}}]
        assert phrasing_overlap(held_out=held) == [held[0]["task"]]

    def test_generation_refuses_to_emit_a_leaking_set(self, monkeypatch):
        """The runner must fail loudly rather than produce contaminated data."""
        import evals.tool_call_data as m
        monkeypatch.setattr(m, "_READ", ("Show me what's in {p}.",))
        assert m.phrasing_overlap() != []
