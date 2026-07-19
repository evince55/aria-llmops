import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals.spec_probe import (
    build_spec_prompt, extract_spec, composite_tier, probe_rows,
    reconstruction_report,
)


# --- prompt ---------------------------------------------------------------

def test_prompt_asks_only_about_specification_not_difficulty():
    p = build_spec_prompt(["do the thing"])
    assert "Ignore how difficult" in p
    assert "UNDERSPECIFIED" in p and "exactly 1 objects" in p


# --- parsing --------------------------------------------------------------

def test_extract_spec_from_tui_chrome_and_fences():
    raw = '\x1b[0m\n> build\n```json\n[{"i":0,"spec":"UNDERSPECIFIED"},{"i":1,"spec":"SPECIFIED"}]\n```'
    assert extract_spec(raw, 2) == {0: "UNDERSPECIFIED", 1: "SPECIFIED"}


def test_extract_spec_rejects_out_of_vocab_and_range():
    raw = '[{"i":0,"spec":"VAGUE"},{"i":7,"spec":"SPECIFIED"},{"i":1,"spec":"SPECIFIED"}]'
    assert extract_spec(raw, 2) == {1: "SPECIFIED"}


# --- the bump rule --------------------------------------------------------

def test_bump_raises_one_step_but_never_into_critical():
    assert composite_tier("SIMPLE", True) == "MODERATE"
    assert composite_tier("MODERATE", True) == "COMPLEX"
    # ambiguity raises capability-needed, not blast radius
    assert composite_tier("COMPLEX", True) == "COMPLEX"
    assert composite_tier("CRITICAL", True) == "CRITICAL"


def test_no_bump_without_a_confirmed_flag():
    assert composite_tier("MODERATE", False) == "MODERATE"
    assert composite_tier("MODERATE", None) == "MODERATE"   # missing flag != flagged


# --- voting ---------------------------------------------------------------

def _probe_with(replies):
    import evals.judge_labels as jl
    orig = jl.call_judge
    def fake(model, prompt, cwd):
        return json.dumps([{"i": i, "spec": v} for i, v in enumerate(replies[model])])
    jl.call_judge = fake
    # spec_probe imported call_judge by value; patch its reference too
    import evals.spec_probe as sp
    orig_sp = sp.call_judge
    sp.call_judge = fake
    try:
        return probe_rows([{"task": "t0"}, {"task": "t1"}], models=tuple(replies))
    finally:
        jl.call_judge = orig
        sp.call_judge = orig_sp


def test_majority_of_three_sets_the_flag():
    rows = _probe_with({
        "opencode-go/a": ["UNDERSPECIFIED", "SPECIFIED"],
        "opencode-go/b": ["UNDERSPECIFIED", "SPECIFIED"],
        "opencode-go/c": ["SPECIFIED", "UNDERSPECIFIED"],
    })
    assert rows[0]["underspecified"] is True
    assert rows[1]["underspecified"] is False


def test_non_opencode_go_labeler_is_refused():
    import pytest
    with pytest.raises(SystemExit):
        probe_rows([{"task": "t"}], models=("zen/some-model",))


# --- reconstruction scoring ----------------------------------------------

def test_reconstruction_counts_baseline_composite_and_flag_separation():
    rows = [
        # baseline miss fixed by the bump: majority MODERATE, operator COMPLEX, flagged
        {"task": "vague one", "expected_tier": "COMPLEX", "underspecified": True,
         "model_votes": {"a": "MODERATE", "b": "MODERATE", "c": "COMPLEX"}},
        # baseline agreement, unflagged: must stay correct
        {"task": "clear one", "expected_tier": "MODERATE", "underspecified": False,
         "model_votes": {"a": "MODERATE", "b": "MODERATE", "c": "SIMPLE"}},
        # baseline agreement that a stray flag would BREAK -> counted against composite
        {"task": "flagged agreement", "expected_tier": "MODERATE", "underspecified": True,
         "model_votes": {"a": "MODERATE", "b": "MODERATE", "c": "MODERATE"}},
    ]
    rep = reconstruction_report(rows)
    assert rep["n"] == 3
    assert rep["baseline_matches"] == 2
    assert rep["composite_matches"] == 2   # +1 fixed miss, -1 broken agreement
    assert rep["flag_fired_on_baseline_misses"] == "1/1"
    assert rep["flag_fired_on_baseline_agreements"] == "1/2"


def test_rows_without_operator_labels_are_excluded_from_scoring():
    rep = reconstruction_report([{"task": "provisional row", "underspecified": True}])
    assert rep["n"] == 0


# --- round 2: method-openness (discovery) mode -----------------------------

def test_discovery_prompt_rules_out_deixis_and_targets_method():
    from evals.spec_probe import build_discovery_prompt
    p = build_discovery_prompt(["improve the thing"])
    assert "Ignore missing context" in p
    assert "DISCOVERY" in p and "diagnose" in p


def test_extract_discovery_reads_the_method_field():
    from evals.spec_probe import extract_discovery
    raw = '[{"i":0,"method":"DISCOVERY"},{"i":1,"method":"DEFINED"},{"i":1,"spec":"SPECIFIED"}]'
    assert extract_discovery(raw, 2) == {0: "DISCOVERY", 1: "DEFINED"}


def test_discovery_mode_flags_on_discovery_majority():
    import json as _json
    import evals.judge_labels as jl, evals.spec_probe as sp
    replies = {"opencode-go/a": ["DISCOVERY"], "opencode-go/b": ["DISCOVERY"],
               "opencode-go/c": ["DEFINED"]}
    def fake(model, prompt, cwd):
        return _json.dumps([{"i": i, "method": v} for i, v in enumerate(replies[model])])
    orig_jl, orig_sp = jl.call_judge, sp.call_judge
    jl.call_judge = sp.call_judge = fake
    try:
        rows = sp.probe_rows([{"task": "t"}], models=tuple(replies), mode="discovery")
    finally:
        jl.call_judge, sp.call_judge = orig_jl, orig_sp
    assert rows[0]["underspecified"] is True and rows[0]["probe_mode"] == "discovery"
