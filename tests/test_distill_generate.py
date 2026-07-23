"""Tests for evals/distill_generate.py — teacher-labeled distillation set builder.

The teacher call is ALWAYS injected (a fake `complete(prompt)->str`); no test
hits the network. These tests pin: seed reading/dedup, verbatim R4 teacher
prompt, robust JSON parsing, seed-tier aggregation, the quarantine guarantee,
provenance tagging, and the jsonl writer.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evals import distill_generate as dg


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _reply(items, noise=True):
    """Serialize teacher items as the model would print them (optionally with
    the opencode TUI header/footer noise around the JSON array)."""
    body = json.dumps(items)
    if noise:
        return f"opencode-go/minimax-m3\n\n  thinking...\n{body}\n\n> done in 2.1s\n"
    return body


def _fake_teacher(mapping):
    """Return a complete(prompt)->str that keys off which seed is in the prompt."""
    def complete(prompt):
        for seed, reply in mapping.items():
            if seed in prompt:
                return reply
        return "NO MATCH"
    return complete


# --------------------------------------------------------------------------- #
# read_seeds
# --------------------------------------------------------------------------- #
def test_read_seeds_extracts_task_text_from_flywheel_jsonl(tmp_path):
    p = tmp_path / "pairs.jsonl"
    p.write_text(
        json.dumps({"task_text": "add a library tab", "tier": "MODERATE"}) + "\n"
        + json.dumps({"task_text": "fix the streaming latency", "tier": "COMPLEX"}) + "\n",
        encoding="utf-8",
    )
    assert dg.read_seeds(p) == ["add a library tab", "fix the streaming latency"]


def test_read_seeds_dedups_preserving_first_seen_order(tmp_path):
    p = tmp_path / "pairs.jsonl"
    p.write_text(
        json.dumps({"task_text": "alpha"}) + "\n"
        + json.dumps({"task_text": "beta"}) + "\n"
        + json.dumps({"task_text": "alpha"}) + "\n",
        encoding="utf-8",
    )
    assert dg.read_seeds(p) == ["alpha", "beta"]


def test_read_seeds_accepts_task_key_and_plaintext_lines(tmp_path):
    p = tmp_path / "seeds.txt"
    p.write_text(
        json.dumps({"task": "labeled-style row"}) + "\n"
        + "a bare plaintext seed line\n"
        + "\n"          # blank line ignored
        + "   \n",       # whitespace-only ignored
        encoding="utf-8",
    )
    assert dg.read_seeds(p) == ["labeled-style row", "a bare plaintext seed line"]


# --------------------------------------------------------------------------- #
# teacher prompt (must be the validated R4 prompt, verbatim)
# --------------------------------------------------------------------------- #
def test_build_prompt_substitutes_placeholders_and_keeps_json_braces():
    out = dg.build_prompt("MY_SEED_TASK", 3)
    assert "MY_SEED_TASK" in out
    assert "{seed}" not in out and "{k}" not in out
    # k appears in both the count instruction and the array-size instruction
    assert "3 realistic PARAPHRASE VARIATIONS" in out
    assert "exactly 3 objects" in out
    # the literal JSON schema braces survive (str.replace, not str.format)
    assert '{"task": "<the variation text>", "tier":' in out


def test_teacher_prompt_carries_router_rubric_verbatim():
    # Guard against drift from the production classifier's intent.
    assert "escalate on\nCONSEQUENCE, not vocabulary" in dg.TEACHER_PROMPT
    for tier in ("SIMPLE", "MODERATE", "COMPLEX", "CRITICAL"):
        assert tier in dg.TEACHER_PROMPT
    assert "renaming an\nAuthManager" in dg.TEACHER_PROMPT


# --------------------------------------------------------------------------- #
# extract_json_array + parse_teacher_reply
# --------------------------------------------------------------------------- #
def test_extract_json_array_ignores_brackets_inside_strings():
    raw = 'header [ {"task": "use arr[0] not arr[1]", "tier": "SIMPLE"} ] footer'
    arr = dg.extract_json_array(raw)
    assert arr == [{"task": "use arr[0] not arr[1]", "tier": "SIMPLE"}]


def test_parse_teacher_reply_returns_empty_on_unparseable_output():
    assert dg.parse_teacher_reply("the model refused, no json here", k=2) == []


def test_parse_teacher_reply_skips_malformed_items_keeps_valid():
    raw = _reply([
        {"task": "good one", "tier": "SIMPLE"},
        {"task": "missing tier"},                       # dropped
        {"task": "", "tier": "MODERATE"},               # empty task -> dropped
        {"task": "bad tier", "tier": "WHATEVER"},       # invalid tier -> dropped
        {"task": "case insensitive", "tier": "complex"},  # normalized -> kept
    ])
    got = dg.parse_teacher_reply(raw, k=5)
    assert got == [
        {"task": "good one", "tier": "SIMPLE"},
        {"task": "case insensitive", "tier": "COMPLEX"},
    ]


# --------------------------------------------------------------------------- #
# seed-tier aggregation
# --------------------------------------------------------------------------- #
def test_seed_tier_is_the_modal_variation_tier():
    assert dg.seed_tier(["COMPLEX", "COMPLEX", "SIMPLE"]) == "COMPLEX"


def test_seed_tier_breaks_ties_toward_most_severe():
    # 1 vs 1 -> the router's severity order wins (CRITICAL > COMPLEX > MODERATE > SIMPLE)
    assert dg.seed_tier(["SIMPLE", "CRITICAL"]) == "CRITICAL"
    assert dg.seed_tier(["MODERATE", "COMPLEX"]) == "COMPLEX"


# --------------------------------------------------------------------------- #
# generate_examples — provenance, seed row, injectable teacher
# --------------------------------------------------------------------------- #
def test_generate_emits_one_seed_row_and_k_synthetic_rows():
    seed = "add a library tab"
    complete = _fake_teacher({seed: _reply([
        {"task": "add a tab to the library", "tier": "MODERATE"},
        {"task": "put a switcher tab up top in the library view", "tier": "MODERATE"},
    ])})
    ex = dg.generate_examples([seed], complete, k=2, teacher="fake-teacher",
                              eval_texts=set(), now=lambda: "2026-07-17T00:00:00+00:00")
    seeds = [e for e in ex if e["source"] == "seed"]
    synth = [e for e in ex if e["source"] == "synthetic"]
    assert len(seeds) == 1 and len(synth) == 2
    s = seeds[0]
    assert s["task"] == seed
    assert s["tier"] == "MODERATE"
    assert s["teacher"] == "fake-teacher"
    assert s["ts"] == "2026-07-17T00:00:00+00:00"
    # every row shares the seed's ref (provenance back to origin seed)
    assert {e["seed_ref"] for e in ex} == {dg.seed_ref(seed)}
    # required keys, exact set
    for e in ex:
        assert set(e.keys()) == {"task", "tier", "source", "teacher", "seed_ref", "ts"}


def test_generate_calls_teacher_exactly_once_per_seed():
    calls = []

    def complete(prompt):
        calls.append(prompt)
        return _reply([{"task": "v1", "tier": "SIMPLE"}])

    dg.generate_examples(["s one", "s two", "s three"], complete, k=1,
                         eval_texts=set(), now=lambda: "T")
    assert len(calls) == 3


def test_generate_skips_whole_seed_when_teacher_output_is_garbage():
    complete = _fake_teacher({"good seed": _reply([{"task": "v", "tier": "SIMPLE"}]),
                              "bad seed": "model returned prose, not json"})
    ex = dg.generate_examples(["good seed", "bad seed"], complete, k=1,
                              eval_texts=set(), now=lambda: "T")
    tasks = {e["task"] for e in ex}
    assert "good seed" in tasks
    assert "bad seed" not in tasks           # no seed row for the unparseable seed
    assert all(dg.seed_ref("bad seed") != e["seed_ref"] for e in ex)


def test_generate_dedups_identical_emitted_tasks():
    seed = "seed text"
    complete = _fake_teacher({seed: _reply([
        {"task": "dup variation", "tier": "SIMPLE"},
        {"task": "dup variation", "tier": "SIMPLE"},   # duplicate -> one row
        {"task": "seed text", "tier": "SIMPLE"},        # equals the seed -> not re-emitted
    ])})
    ex = dg.generate_examples([seed], complete, k=3, eval_texts=set(),
                              now=lambda: "T")
    tasks = [e["task"] for e in ex]
    assert tasks.count("dup variation") == 1
    assert tasks.count("seed text") == 1   # only the seed row, not also a synthetic


# --------------------------------------------------------------------------- #
# QUARANTINE — non-negotiable
# --------------------------------------------------------------------------- #
def test_generate_skips_a_seed_that_is_in_the_eval_set():
    evalset = {"held out eval task"}
    complete = _fake_teacher({"held out eval task": _reply([{"task": "x", "tier": "SIMPLE"}])})
    ex = dg.generate_examples(["held out eval task"], complete, k=1,
                              eval_texts=evalset, now=lambda: "T")
    assert ex == []


def test_generate_drops_a_variation_that_collides_with_the_eval_set():
    seed = "novel seed"
    evalset = {"this is a held-out eval task"}
    complete = _fake_teacher({seed: _reply([
        {"task": "this is a held-out eval task", "tier": "SIMPLE"},   # collides -> dropped
        {"task": "a safe variation", "tier": "SIMPLE"},
    ])})
    ex = dg.generate_examples([seed], complete, k=2, eval_texts=evalset,
                              now=lambda: "T")
    emitted = {e["task"] for e in ex}
    assert "this is a held-out eval task" not in emitted
    assert "a safe variation" in emitted
    assert seed in emitted                       # seed row survives
    # the non-negotiable property: zero overlap with the eval set
    assert emitted.isdisjoint(evalset)


def test_eval_task_texts_loads_the_real_labeled_sets():
    texts = dg.eval_task_texts()
    assert len(texts) > 0
    # a known member of evals/datasets/labeled_tasks.jsonl
    assert "fix a typo in the README" in texts


# --------------------------------------------------------------------------- #
# write_jsonl
# --------------------------------------------------------------------------- #
def test_write_jsonl_roundtrips_and_creates_parent_dir(tmp_path):
    out = tmp_path / "distilled" / "train.jsonl"
    examples = [
        {"task": "t1", "tier": "SIMPLE", "source": "seed",
         "teacher": "m", "seed_ref": "abc", "ts": "T"},
        {"task": "t2", "tier": "COMPLEX", "source": "synthetic",
         "teacher": "m", "seed_ref": "abc", "ts": "T"},
    ]
    n = dg.write_jsonl(examples, out)
    assert n == 2
    assert out.exists()
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows == examples


# --- scope-drift guard (A3-adapted: the filter threshold gates label integrity) ---

def test_scope_band_accepts_terse_and_verbose_faithful_variations():
    from evals.distill_generate import within_scope_band
    seed = "fix the race condition in the playback queue when the app backgrounds"
    assert within_scope_band(seed, "fix the queue race on backgrounding")          # terse
    assert within_scope_band(seed, "There's a race condition in the playback "
                                   "queue that shows up when the app moves to the "
                                   "background — please track it down and fix it.")  # verbose


def test_scope_band_rejects_ballooned_variation_that_added_scope():
    from evals.distill_generate import within_scope_band
    seed = "rename currentTrack to currentSong in PlayerManager"
    ballooned = ("rename currentTrack to currentSong in PlayerManager, then audit every "
                 "call site across the app, add migration code for persisted state, write "
                 "unit tests for the migration, update the docs, and refactor the "
                 "surrounding queue logic while you are in there because it is messy, and "
                 "also add analytics events for the rename so we can track adoption") 
    assert not within_scope_band(seed, ballooned)


def test_scope_band_rejects_gutted_variation():
    from evals.distill_generate import within_scope_band
    seed = ("Investigate why /api/resolve returns a 500 only for video ids containing a "
            "hyphen, trace the encoding bug and add a regression test")
    assert not within_scope_band(seed, "fix it")


def test_generate_drops_scope_drifted_variations(monkeypatch):
    from evals.distill_generate import generate_examples
    import json as _json
    seed = "add a settings toggle for offline mode in SettingsView"
    balloon = seed + " " + ("and also rebuild the entire settings architecture " * 8)
    reply = _json.dumps([{"task": "add an offline-mode toggle to SettingsView", "tier": "MODERATE"},
                         {"task": balloon, "tier": "MODERATE"}])
    ex = generate_examples([seed], lambda p: reply, k=2, eval_texts=set())
    tasks = [e["task"] for e in ex]
    assert "add an offline-mode toggle to SettingsView" in tasks
    assert balloon not in tasks   # scope-drifted variation dropped
