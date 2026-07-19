"""Pre-label harvested eval tasks and route the contested ones to a human.

This produces the labels for a HELD-OUT EVAL SET, which makes it a different
problem from labeling training data:

* **Labeler independence is mandatory.** A grader may not use any component of
  the system under test. Labeling with the keyword classifier would make the
  hybrid router score against its own output; labeling with the 9B or a tuned
  E2B does the same for those. So only opencode-go cloud models label here, and
  the module refuses anything else.

* **Disagreement is kept, not dropped.** ``judge_labels`` drops contested rows
  because a noisy training label is simply bad data. For an eval set the
  contested rows are the *most* valuable ones -- they are the genuinely
  ambiguous boundary cases -- so they go to a human review queue instead.

The S6 audit showed a fixed judge pair shares blind spots (73 downgrades vs 5
upgrades, grading diff size rather than consequence). Two mitigations: three
labs instead of two, and ``_GUARD`` below, the explicit correction that audit
recommended.

Output is deliberately two files: a provisional set the models agree on, and a
review queue. The eval set is only finished once a human has worked the queue.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.judge_labels import RUBRIC, judge_rows  # noqa: E402

# Three distinct labs, so a shared training lineage is less likely to produce a
# shared blind spot. All opencode-go routes; zen models are never used.
EVAL_LABELERS = (
    "opencode-go/minimax-m3",
    "opencode-go/deepseek-v4-pro",
    "opencode-go/glm-5.2",
)

# The correction the S6 tier audits asked for. Both judges there repeatedly
# labeled one-line XSS, leaked-token, and OAuth-fragment fixes as SIMPLE because
# the patch was small -- grading mechanism instead of consequence.
_GUARD = """
IMPORTANT — two failure modes to avoid:
1. Diff size NEVER caps the tier. A security/auth/data-exposure hole, a
   destructive migration, or anything causing production downtime is CRITICAL
   even when the fix is one line.
2. Do not inflate on urgent-sounding wording. Routine work described dramatically
   is still routine. Grade the CONSEQUENCE of getting it wrong, not the tone.
"""

GRADED_RUBRIC = RUBRIC + _GUARD


def build_gate_prompt(tasks: list) -> str:
    """Ask whether each candidate is a routable engineering task at all.

    Harvested transcript messages include approvals ("Proceed with merge, it
    works fine."), meta-instructions about the agent, and mid-conversation
    replies that depend on context the classifier will never see. Regex catches
    the obvious ones; this catches the rest. Grading a router on a non-task
    measures nothing.
    """
    lines = [
        "For each item, decide whether it is a SELF-CONTAINED software "
        "engineering task that a coding assistant could act on.",
        "",
        "NO (not a task) if it is: an approval or reply to a previous message; "
        "an instruction about how the assistant should behave; a question about "
        "status; or a request that cannot be understood without earlier "
        "conversation (e.g. 'proceed with that', 'do #2 as well').",
        "YES only if the item states, on its own, work to be done to software.",
        "",
        "ITEMS:",
    ]
    for i, task in enumerate(tasks):
        lines.append(f"{i}. {task}")
    lines += [
        "",
        'Reply with ONLY a strict JSON array, no prose: [{"i":0,"is_task":true}, ...]',
        f"Return exactly {len(tasks)} objects.",
    ]
    return "\n".join(lines)


def extract_flags(raw: str, n: int) -> dict:
    """Parse ``{index: bool}`` from a gate reply (same TUI-chrome tolerance as
    ``judge_labels.extract_labels``)."""
    import re
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", raw or "")
    best: dict = {}
    for match in re.finditer(r"\[.*?\]", text, re.DOTALL):
        try:
            arr = json.loads(match.group(0))
        except (ValueError, TypeError):
            continue
        if not isinstance(arr, list):
            continue
        got = {}
        for obj in arr:
            if isinstance(obj, dict) and isinstance(obj.get("i"), int) \
                    and isinstance(obj.get("is_task"), bool) and 0 <= obj["i"] < n:
                got[obj["i"]] = obj["is_task"]
        if len(got) > len(best):
            best = got
    return best


def gate_tasks(rows, models=EVAL_LABELERS, batch_size: int = 20,
               cwd: Path = Path(".")) -> tuple:
    """Majority-vote validity gate. Returns ``(kept_rows, rejected_rows)``.

    A row survives on a strict majority of YES votes; ties and unlabeled rows
    are rejected, because an eval row nobody is confident is a task is not worth
    a human's review time.
    """
    import evals.judge_labels as jl
    tasks = [r["task"] for r in rows]
    votes: dict = {i: [] for i in range(len(tasks))}
    for model in models:
        for start in range(0, len(tasks), batch_size):
            chunk = tasks[start:start + batch_size]
            prompt = build_gate_prompt(chunk)
            flags = extract_flags(jl.call_judge(model, prompt, cwd), len(chunk))
            for local_i, flag in flags.items():
                votes[start + local_i].append(flag)

    kept, rejected = [], []
    for i, row in enumerate(rows):
        yes = sum(1 for v in votes[i] if v)
        total = len(votes[i])
        (kept if total and yes * 2 > total else rejected).append(
            {**row, "gate_yes": yes, "gate_votes": total})
    return kept, rejected


def split_by_agreement(result, rows) -> tuple:
    """Split a ``judge_rows`` result into (provisional, review_queue).

    ``provisional`` = unanimous rows carrying the agreed tier.
    ``review_queue`` = contested rows carrying every model's vote, ordered so a
    human sees the widest disagreements first (3 distinct votes before 2).
    """
    provisional = [
        {"task": r["task"], "expected_tier": r["tier"],
         "source": "harvested-human+model-consensus", "labelers": r["judges"]}
        for r in result["kept"]
    ]
    by_task = {r["task"]: r for r in rows}
    queue = []
    for d in result["dropped"]:
        votes = {m.split("/")[-1]: t for m, t in d["votes"].items()}
        queue.append({
            "task": d["task"],
            "votes": votes,
            "distinct": len(set(votes.values())),
            "origin": (by_task.get(d["task"]) or {}).get("origin", ""),
            "expected_tier": None,          # a human fills this in
        })
    queue.sort(key=lambda q: -q["distinct"])
    return provisional, queue


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Pre-label harvested eval tasks; queue disagreements for review")
    p.add_argument("--in-file", default=str(repo / "evals/datasets/eval_candidates.jsonl"))
    p.add_argument("--provisional", default=str(repo / "evals/datasets/eval_provisional.jsonl"))
    p.add_argument("--review-queue", default=str(repo / "evals/datasets/eval_review_queue.jsonl"))
    p.add_argument("--models", default=",".join(EVAL_LABELERS))
    p.add_argument("--batch-size", type=int, default=20)
    a = p.parse_args(argv)

    models = tuple(m.strip() for m in a.models.split(",") if m.strip())
    for m in models:
        if not m.startswith("opencode-go/"):
            raise SystemExit(f"refusing non-opencode-go labeler: {m}")
        if any(bad in m for bad in ("qwen3.5-9b", "gemma-4-e2b", "gemma-4-e4b")):
            raise SystemExit(f"refusing a labeler that is under evaluation: {m}")

    rows = [json.loads(line) for line in Path(a.in_file).read_text().splitlines() if line.strip()]

    # judge_labels builds its prompt from RUBRIC; swap in the guarded version.
    import evals.judge_labels as jl
    jl.RUBRIC = GRADED_RUBRIC

    # Pass 1: is it a routable task at all? Tiering a non-task is meaningless.
    rows, rejected = gate_tasks(rows, models=models, batch_size=a.batch_size, cwd=repo)
    Path(a.review_queue).with_name("eval_rejected_nontasks.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rejected))

    # Pass 2: tier the survivors.
    result = judge_rows(rows, models=models, batch_size=a.batch_size, cwd=repo)
    provisional, queue = split_by_agreement(result, rows)

    Path(a.provisional).write_text("".join(json.dumps(r) + "\n" for r in provisional))
    Path(a.review_queue).write_text("".join(json.dumps(r) + "\n" for r in queue))

    print(json.dumps({
        "gate_rejected_nontasks": len(rejected),
        "n_in": len(rows),
        "unanimous": len(provisional),
        "needs_human_review": len(queue),
        "unanimous_rate": round(len(provisional) / max(len(rows), 1), 3),
        "provisional_by_tier": dict(Counter(r["expected_tier"] for r in provisional)),
        "queue_by_spread": dict(Counter(q["distinct"] for q in queue)),
        "unlabeled": result["unlabeled"],
        "provisional_file": a.provisional,
        "review_queue_file": a.review_queue,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
