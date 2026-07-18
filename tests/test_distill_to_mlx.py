import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from evals.distill_to_mlx import to_message_example, split_stratified, convert


def _row(task, tier, source="synthetic"):
    return {"task": task, "tier": tier, "source": source}


def test_message_example_is_user_prompt_then_assistant_tier():
    ex = to_message_example(_row("rename a var", "SIMPLE"))
    msgs = ex["messages"]
    assert msgs[0]["role"] == "user" and "rename a var" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant" and msgs[1]["content"] == "SIMPLE"
    # user turn carries the shared rubric prompt (so train prompt == eval prompt)
    from llmops import _CLASSIFY_PROMPT
    assert _CLASSIFY_PROMPT.split("{task}")[0][:20] in msgs[0]["content"]


def test_invalid_tier_row_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        to_message_example(_row("x", "URGENT"))


def test_split_is_deterministic_and_disjoint():
    rows = [_row(f"task number {i}", "SIMPLE") for i in range(20)]
    a_tr, a_va = split_stratified(rows, valid_frac=0.2, seed=0)
    b_tr, b_va = split_stratified(rows, valid_frac=0.2, seed=0)
    assert [r["task"] for r in a_va] == [r["task"] for r in b_va]  # deterministic
    tr_tasks = {r["task"] for r in a_tr}
    va_tasks = {r["task"] for r in a_va}
    assert tr_tasks.isdisjoint(va_tasks)
    assert len(a_tr) + len(a_va) == 20 and len(a_va) == 4


def test_split_stratifies_by_tier():
    rows = [_row(f"s{i}", "SIMPLE") for i in range(10)] + [_row(f"c{i}", "CRITICAL") for i in range(10)]
    tr, va = split_stratified(rows, valid_frac=0.2, seed=0)
    # each tier contributes to valid (2 SIMPLE + 2 CRITICAL), not all-from-one-tier
    va_tiers = [r["tier"] for r in va]
    assert va_tiers.count("SIMPLE") == 2 and va_tiers.count("CRITICAL") == 2


def test_convert_writes_train_and_valid_chat_jsonl(tmp_path):
    src = tmp_path / "distilled.jsonl"
    rows = [_row(f"task {i}", "SIMPLE") for i in range(8)] + [_row(f"crit {i}", "CRITICAL") for i in range(8)]
    src.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    out = tmp_path / "data"
    summary = convert(src, out, valid_frac=0.25, seed=0)
    tr = [json.loads(l) for l in (out / "train.jsonl").read_text().splitlines()]
    va = [json.loads(l) for l in (out / "valid.jsonl").read_text().splitlines()]
    assert len(tr) == 12 and len(va) == 4
    assert all("messages" in e and len(e["messages"]) == 2 for e in tr + va)
    assert summary["train"] == 12 and summary["valid"] == 4
