"""Apply the pre-registered ensemble rules to saved per-arm runs.

Reads arm logs (each produced by `tool_call_eval.py` or `tool_call_native.py`,
both of which record a per-row `got`) and combines them under the rules fixed in
`docs/research/2026-07-26-s10-ensemble-preregistration.md`.

Rows are joined BY TASK TEXT, not by index. A missing task in any arm is a hard
error rather than a silent drop: an ensemble scored over a different row set than
its members is not comparable to them, and it would read as a clean gain.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.ensemble import agree_or_escalate, cascade_score, majority, vote_score  # noqa: E402
from evals.tool_calls import HARD_TASKS, TASKS  # noqa: E402


def load_arm(path: str) -> dict:
    """Map task text -> the call that arm emitted (None if it produced none)."""
    data = json.loads(Path(path).read_text())
    return {r["task"]: r.get("got") for r in data["rows"]}


def combine(arm_paths, tasks) -> dict:
    arms = [load_arm(p) for p in arm_paths]
    for path, arm in zip(arm_paths, arms):
        missing = [t["task"] for t in tasks if t["task"] not in arm]
        if missing:
            raise SystemExit(f"{path} is missing {len(missing)} task(s), e.g. {missing[0]!r}")

    cascade_rows, vote_rows, detail = [], [], []
    for t in tasks:
        truth = {"tool": t["tool"], "args": t["args"]}
        calls = [arm[t["task"]] for arm in arms]
        # Rule A uses the two primary arms; a third opinion would make it a vote.
        a = agree_or_escalate(calls[:2])
        a["truth"] = truth
        cascade_rows.append(a)
        v = {"call": majority(calls) if len(calls) >= 3 else None, "truth": truth}
        vote_rows.append(v)
        detail.append({"task": t["task"], "truth": truth, "calls": calls,
                       "accepted": a["accepted"], "majority": v["call"]})

    out = {"n": len(tasks), "arms": list(arm_paths),
           "rule_a_agree_or_escalate": cascade_score(cascade_rows), "detail": detail}
    if len(arms) >= 3:
        out["rule_b_majority_vote"] = vote_score(vote_rows)
    return out


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="S10: apply the pre-registered ensemble rules")
    p.add_argument("--arm", action="append", required=True,
                   help="path to an arm's run log; repeat. First two are Rule A.")
    p.add_argument("--set", choices=("standard", "adversarial"), default="adversarial")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)

    tasks = TASKS if a.set == "standard" else HARD_TASKS
    res = combine(a.arm, tasks)
    out = Path(a.out or repo / f"logs/s10_ensemble_{a.set}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2))
    print(json.dumps({k: v for k, v in res.items() if k != "detail"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
