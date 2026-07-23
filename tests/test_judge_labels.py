import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals.judge_labels import build_prompt, extract_labels, judge_rows


def _row(task, tier, domain="iOS/Swift app"):
    return {"task": task, "tier": tier, "domain": domain}


# --- prompt ---------------------------------------------------------------

def test_prompt_numbers_tasks_and_states_expected_count():
    p = build_prompt(["rename a var", "fix an auth hole"])
    assert "0. rename a var" in p and "1. fix an auth hole" in p
    assert "exactly 2 objects" in p  # lets a short reply be detected


# --- parsing (the misalignment-risk surface) ------------------------------

def test_extracts_json_from_opencode_tui_chrome():
    raw = '\x1b[0m\n> build \xb7 minimax-m3\n\x1b[0m\n[{"i":0,"tier":"COMPLEX"},{"i":1,"tier":"SIMPLE"}]'
    assert extract_labels(raw, 2) == {0: "COMPLEX", 1: "SIMPLE"}


def test_extracts_from_markdown_fenced_reply():
    raw = 'Sure!\n```json\n[{"i":0,"tier":"CRITICAL"}]\n```\n'
    assert extract_labels(raw, 1) == {0: "CRITICAL"}


def test_prefers_the_longest_valid_array_when_several_appear():
    # models sometimes echo a short example array from the prompt first
    raw = '[{"i":0,"tier":"SIMPLE"}] then really: [{"i":0,"tier":"MODERATE"},{"i":1,"tier":"COMPLEX"}]'
    assert extract_labels(raw, 2) == {0: "MODERATE", 1: "COMPLEX"}


def test_out_of_range_and_invalid_tiers_are_discarded():
    raw = '[{"i":0,"tier":"SIMPLE"},{"i":9,"tier":"SIMPLE"},{"i":1,"tier":"URGENT"}]'
    assert extract_labels(raw, 2) == {0: "SIMPLE"}  # i=9 out of range, URGENT not a tier


def test_unparseable_reply_yields_no_labels():
    assert extract_labels("the models are down, sorry", 3) == {}
    assert extract_labels("", 3) == {}


# --- agreement policy -----------------------------------------------------

def _fake(mapping):
    """judge_rows with call_judge stubbed: mapping[model] -> list of tiers."""
    import evals.judge_labels as jl
    import json as _json

    def fake_call(model, prompt, cwd):
        tiers = mapping[model]
        return _json.dumps([{"i": i, "tier": t} for i, t in enumerate(tiers)])

    orig, jl.call_judge = jl.call_judge, fake_call
    try:
        rows = [_row(f"task {i}", "COMPLEX") for i in range(len(next(iter(mapping.values()))))]
        return jl.judge_rows(rows, models=tuple(mapping))
    finally:
        jl.call_judge = orig


def test_only_unanimous_rows_are_kept():
    res = _fake({"a": ["COMPLEX", "SIMPLE"], "b": ["COMPLEX", "MODERATE"]})
    assert [k["task"] for k in res["kept"]] == ["task 0"]
    assert len(res["dropped"]) == 1
    assert res["agreement_rate"] == 0.5


def test_agreed_label_overrides_generator_intent_and_is_counted():
    # both judges say SIMPLE though the generator intended COMPLEX
    res = _fake({"a": ["SIMPLE"], "b": ["SIMPLE"]})
    kept = res["kept"][0]
    assert kept["tier"] == "SIMPLE" and kept["intent_tier"] == "COMPLEX"
    assert res["relabeled"] == 1
    # the systematic-drift signal: intended COMPLEX landed as SIMPLE
    assert res["intent_confusion"]["COMPLEX"]["SIMPLE"] == 1


def test_rows_no_judge_could_label_are_reported_not_kept():
    import evals.judge_labels as jl
    orig, jl.call_judge = jl.call_judge, lambda model, prompt, cwd: "garbage"
    try:
        res = jl.judge_rows([_row("t", "SIMPLE")], models=("a", "b"))
    finally:
        jl.call_judge = orig
    assert res["kept"] == [] and res["unlabeled"] == 1
    assert res["agreement_rate"] == 0.0  # no division-by-zero on a fully failed shard


# --- eval-set labeling (labeler independence + review queue) ---------------

def test_eval_labelers_are_three_distinct_opencode_go_labs():
    from evals.label_eval_set import EVAL_LABELERS
    assert len(EVAL_LABELERS) == 3
    assert all(m.startswith("opencode-go/") for m in EVAL_LABELERS)
    assert len({m.split("/")[-1].split("-")[0] for m in EVAL_LABELERS}) == 3


def test_guarded_rubric_states_diff_size_never_caps_tier():
    from evals.label_eval_set import GRADED_RUBRIC
    assert "NEVER caps the tier" in GRADED_RUBRIC
    assert "one line" in GRADED_RUBRIC


def test_split_puts_unanimous_in_provisional_and_contested_in_queue():
    from evals.label_eval_set import split_by_agreement
    rows = [{"task": "a", "origin": "transcript"}, {"task": "b", "origin": "telemetry"}]
    result = {
        "kept": [{"task": "a", "tier": "COMPLEX", "judges": "m1+m2+m3"}],
        "dropped": [{"task": "b", "votes": {"x/m1": "SIMPLE", "x/m2": "MODERATE", "x/m3": "COMPLEX"}}],
    }
    prov, queue = split_by_agreement(result, rows)
    assert prov[0]["expected_tier"] == "COMPLEX"
    assert queue[0]["task"] == "b" and queue[0]["distinct"] == 3
    assert queue[0]["expected_tier"] is None       # a human must decide
    assert queue[0]["origin"] == "telemetry"


def test_review_queue_puts_widest_disagreements_first():
    from evals.label_eval_set import split_by_agreement
    result = {"kept": [], "dropped": [
        {"task": "two-way", "votes": {"a/m1": "SIMPLE", "a/m2": "SIMPLE", "a/m3": "MODERATE"}},
        {"task": "three-way", "votes": {"a/m1": "SIMPLE", "a/m2": "MODERATE", "a/m3": "CRITICAL"}},
    ]}
    _, queue = split_by_agreement(result, [])
    assert [q["task"] for q in queue] == ["three-way", "two-way"]


# --- validity gate ---------------------------------------------------------

def test_gate_prompt_defines_non_tasks():
    from evals.label_eval_set import build_gate_prompt
    p = build_gate_prompt(["Proceed with merge"])
    assert "SELF-CONTAINED" in p and "approval" in p and "exactly 1 objects" in p


def test_extract_flags_parses_booleans_from_tui_chrome():
    from evals.label_eval_set import extract_flags
    raw = '\x1b[0m\n> build\n[{"i":0,"is_task":true},{"i":1,"is_task":false}]'
    assert extract_flags(raw, 2) == {0: True, 1: False}


def test_extract_flags_ignores_non_boolean_and_out_of_range():
    from evals.label_eval_set import extract_flags
    assert extract_flags('[{"i":0,"is_task":"yes"},{"i":5,"is_task":true}]', 2) == {}


def test_gate_keeps_majority_yes_and_rejects_ties():
    import evals.judge_labels as jl
    from evals.label_eval_set import gate_tasks
    import json as _json
    # model order: 2 yes / 1 no for item 0; 1 yes / 2 no for item 1
    replies = {"m1": [True, True], "m2": [True, False], "m3": [False, False]}
    orig = jl.call_judge
    jl.call_judge = lambda model, prompt, cwd: _json.dumps(
        [{"i": i, "is_task": v} for i, v in enumerate(replies[model])])
    try:
        kept, rejected = gate_tasks([{"task": "a"}, {"task": "b"}], models=("m1", "m2", "m3"))
    finally:
        jl.call_judge = orig
    assert [r["task"] for r in kept] == ["a"]
    assert [r["task"] for r in rejected] == ["b"]
    assert kept[0]["gate_yes"] == 2 and kept[0]["gate_votes"] == 3


def test_gate_rejects_rows_no_model_could_judge():
    import evals.judge_labels as jl
    from evals.label_eval_set import gate_tasks
    orig = jl.call_judge
    jl.call_judge = lambda model, prompt, cwd: "garbage"
    try:
        kept, rejected = gate_tasks([{"task": "a"}], models=("m1",))
    finally:
        jl.call_judge = orig
    assert kept == [] and len(rejected) == 1
