import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals.apply_label_policy import (
    resolve_label, is_carve_out, dedup, balance, assemble, eval_task_set,
)


def _judged(task, agreed, intent):
    return {"task": task, "tier": agreed, "intent_tier": intent, "source": "synthetic-v2"}


# --- the policy itself ----------------------------------------------------

def test_confirmed_label_is_kept():
    tier, why = resolve_label(_judged("t", "COMPLEX", "COMPLEX"))
    assert (tier, why) == ("COMPLEX", "confirmed")


def test_upgrade_is_kept_judges_beat_generator_when_escalating():
    tier, why = resolve_label(_judged("t", "CRITICAL", "COMPLEX"))
    assert (tier, why) == ("CRITICAL", "upgrade-kept")


def test_downgrade_reverts_to_intent():
    # the failure mode this policy exists for: a one-line XSS fix judged SIMPLE
    row = _judged("drops location.hash into innerHTML - DOM XSS", "SIMPLE", "CRITICAL")
    tier, why = resolve_label(row)
    assert (tier, why) == ("CRITICAL", "downgrade-reverted")


def test_carve_out_downgrade_is_respected():
    row = _judged("PlayerManager mutates its `queue` array from the main actor and ...",
                  "COMPLEX", "CRITICAL")
    tier, why = resolve_label(row)
    assert (tier, why) == ("COMPLEX", "downgrade-kept-carveout")


def test_carve_out_matching_is_whitespace_and_case_insensitive():
    assert is_carve_out("playermanager   MUTATES its `queue`   ARRAY from a callback")


def test_row_without_intent_is_left_alone():
    tier, why = resolve_label({"task": "t", "tier": "MODERATE"})
    assert (tier, why) == ("MODERATE", "confirmed")


# --- assembly hygiene -----------------------------------------------------

def test_dedup_drops_exact_and_prefix_duplicates():
    rows = [
        {"task": "Add a cache to /api/stats", "tier": "MODERATE"},
        {"task": "  add a CACHE to /api/stats  ", "tier": "SIMPLE"},   # exact after norm
        {"task": "x" * 100 + " tail A", "tier": "SIMPLE"},
        {"task": "x" * 100 + " tail B", "tier": "SIMPLE"},             # same first 100 chars
        {"task": "Something else entirely", "tier": "SIMPLE"},
    ]
    assert [r["task"] for r in dedup(rows)][0] == "Add a cache to /api/stats"
    assert len(dedup(rows)) == 3


def test_balance_caps_each_tier_independently():
    rows = [{"task": f"t{i}", "tier": "MODERATE"} for i in range(10)]
    rows += [{"task": f"c{i}", "tier": "COMPLEX"} for i in range(3)]
    out = balance(rows, cap=4)
    from collections import Counter
    assert Counter(r["tier"] for r in out) == {"MODERATE": 4, "COMPLEX": 3}


# --- the non-negotiable invariant ----------------------------------------

def test_eval_tasks_are_quarantined_out_of_the_training_set(tmp_path):
    judged = tmp_path / "judged"; judged.mkdir()
    (judged / "a.jsonl").write_text(
        json.dumps(_judged("HELD OUT eval task", "SIMPLE", "SIMPLE")) + "\n" +
        json.dumps(_judged("a genuinely new task", "MODERATE", "MODERATE")) + "\n"
    )
    evald = tmp_path / "evals"; evald.mkdir()
    # normalization must catch it despite different case/whitespace
    (evald / "labeled_tasks.jsonl").write_text(json.dumps({"task": "held out   EVAL task"}) + "\n")
    s5 = tmp_path / "s5.jsonl"; s5.write_text("")
    out = tmp_path / "train_v2.jsonl"

    rep = assemble(judged, s5, evald, out)
    tasks = [json.loads(l)["task"] for l in out.read_text().splitlines() if l.strip()]
    assert tasks == ["a genuinely new task"]
    assert rep["quarantine_removed"] == 1 and rep["quarantine_overlap_after"] == 0


def test_eval_union_reads_every_labeled_tasks_file(tmp_path):
    d = tmp_path; (d / "labeled_tasks.jsonl").write_text(json.dumps({"task": "one"}) + "\n")
    (d / "labeled_tasks_prose.jsonl").write_text(json.dumps({"task": "two"}) + "\n")
    (d / "labeled_tasks_balanced.jsonl").write_text(json.dumps({"task": "three"}) + "\n")
    assert eval_task_set(d) == {"one", "two", "three"}


def test_no_policy_mode_keeps_agreed_labels(tmp_path):
    judged = tmp_path / "judged"; judged.mkdir()
    (judged / "a.jsonl").write_text(json.dumps(_judged("xss one-liner", "SIMPLE", "CRITICAL")) + "\n")
    evald = tmp_path / "evals"; evald.mkdir()
    s5 = tmp_path / "s5.jsonl"; s5.write_text("")
    out = tmp_path / "v.jsonl"
    rep = assemble(judged, s5, evald, out, apply_policy=False)
    assert rep["by_tier"] == {"SIMPLE": 1}  # ablation baseline keeps the judge's call
