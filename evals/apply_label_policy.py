"""Final label policy + assembly for the S6 scaled training set.

Cross-model judging (``judge_labels.py``) gave every task a label two
opencode-go models agreed on. An independent per-tier audit of those survivors
then found a specific, reproducible failure in that agreement:

    The agreed label is reliable when the judges CONFIRM the generator's
    intent, and unreliable when they OVERRIDE it -- and overrides are almost
    entirely one-directional. Measured on the 495 judged rows: 73 downgrades
    vs 5 upgrades (94% downward).

Root cause, named independently by two auditors: the judges grade MECHANISM and
DIFF SIZE, not CONSEQUENCE. A one-line fix for DOM-XSS, a leaked bearer token,
or an OAuth token in the URL fragment was labeled SIMPLE because the patch is
small -- while the rubric says to escalate on consequence, not effort. Both
judges are the same fixed pair on every row, so this is a shared blind spot
that agreement cannot detect; only the out-of-band audit surfaced it.

For a ROUTER the asymmetry decides the policy. Over-escalation wastes money on
a stronger model. Under-escalation sends XSS, credential-leak, and destructive
migration work to the cheapest local tier -- the most expensive error this
classifier can make. So downgrades are reverted to intent and upgrades are
kept (the audit confirmed all 5 upgrades were correct).

CARVE_OUTS holds the downgrades the audit affirmed as correct. It is
deliberately tiny and each entry is justified: a blanket rule with a documented
exception list beats per-row hand-editing, which is neither reviewable nor
reproducible.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import _TIERS  # noqa: E402

# Rubric severity order -- "downgrade" means the judges moved a task DOWN this scale.
TIER_ORDER = {"SIMPLE": 0, "MODERATE": 1, "COMPLEX": 2, "CRITICAL": 3}

# Downgrades the audit affirmed. Matched on a distinctive task substring.
# Only rows where BOTH the tier-owning auditor and the CRITICAL auditor agreed
# the judges were right belong here.
CARVE_OUTS = (
    # Recoverable crash: no persisted corruption, no data exposure, no money.
    # CRITICAL was over-labeled by the generator; COMPLEX is correct.
    "PlayerManager mutates its `queue` array",
)

TIER_CAP = 300      # no tier may dominate
TIER_FLOOR = 100    # below this, a tier is reported short rather than padded


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_carve_out(task: str) -> bool:
    return any(_norm(marker) in _norm(task) for marker in CARVE_OUTS)


def resolve_label(row: dict) -> tuple:
    """Final tier for one judged row, plus why.

    Returns ``(tier, reason)`` where reason is one of ``confirmed`` (judges
    agreed with intent), ``upgrade-kept``, ``downgrade-reverted``, or
    ``downgrade-kept-carveout``.
    """
    agreed = row.get("tier")
    intent = row.get("intent_tier") or agreed
    if intent not in TIER_ORDER or agreed not in TIER_ORDER:
        return agreed, "confirmed"
    if agreed == intent:
        return agreed, "confirmed"
    if TIER_ORDER[agreed] > TIER_ORDER[intent]:
        return agreed, "upgrade-kept"
    if is_carve_out(row.get("task", "")):
        return agreed, "downgrade-kept-carveout"
    return intent, "downgrade-reverted"


def load_jsonl(path: Path) -> list:
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def eval_task_set(eval_glob_dir: Path) -> set:
    """Normalized text of every held-out eval task. Training on any of these
    would invalidate the gate the S6 result is measured by."""
    quarantined = set()
    for path in sorted(eval_glob_dir.glob("labeled_tasks*.jsonl")):
        for row in load_jsonl(path):
            text = row.get("task") or row.get("text") or ""
            if text:
                quarantined.add(_norm(text))
    return quarantined


def dedup(rows: list) -> list:
    """Drop exact duplicates and near-duplicates (identical first 100 chars),
    keeping first occurrence."""
    seen_exact, seen_prefix, out = set(), set(), []
    for row in rows:
        key = _norm(row.get("task", ""))
        if not key or key in seen_exact:
            continue
        prefix = key[:100]
        if prefix in seen_prefix:
            continue
        seen_exact.add(key)
        seen_prefix.add(prefix)
        out.append(row)
    return out


def balance(rows: list, cap: int = TIER_CAP) -> list:
    """Cap each tier. Rows arrive dedup'd; we keep the earliest of each tier,
    which preserves the domain interleaving of the judged files."""
    kept, counts = [], Counter()
    for row in rows:
        tier = row["tier"]
        if counts[tier] >= cap:
            continue
        counts[tier] += 1
        kept.append(row)
    return kept


def assemble(judged_dir: Path, s5_train: Path, eval_dir: Path, out_path: Path,
             apply_policy: bool = True, cap: int = TIER_CAP) -> dict:
    judged = []
    for path in sorted(judged_dir.glob("*.jsonl")):
        judged.extend(load_jsonl(path))

    reasons = Counter()
    staged = []
    for row in judged:
        if apply_policy:
            tier, reason = resolve_label(row)
        else:
            tier, reason = row.get("tier"), "agreed-as-judged"
        reasons[reason] += 1
        staged.append({"task": row["task"], "tier": tier, "source": "synthetic-v2"})

    s5 = [{"task": r["task"], "tier": r["tier"], "source": r.get("source", "synthetic")}
          for r in load_jsonl(s5_train) if r.get("tier") in _TIERS and r.get("task")]

    merged = dedup(staged + s5)

    quarantined = eval_task_set(eval_dir)
    before = len(merged)
    merged = [r for r in merged if _norm(r["task"]) not in quarantined]
    removed = before - len(merged)

    final = balance(merged, cap=cap)

    leaked = [r for r in final if _norm(r["task"]) in quarantined]
    if leaked:  # the gate would be meaningless; refuse to write
        raise SystemExit(f"QUARANTINE FAILURE: {len(leaked)} eval tasks in training set")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in final:
            fh.write(json.dumps(row) + "\n")

    by_tier = Counter(r["tier"] for r in final)
    return {
        "out": str(out_path),
        "total": len(final),
        "by_tier": dict(by_tier),
        "by_source": dict(Counter(r["source"] for r in final)),
        "policy_actions": dict(reasons),
        "quarantine_removed": removed,
        "quarantine_overlap_after": 0,
        "tiers_below_floor": {t: TIER_FLOOR - by_tier.get(t, 0)
                              for t in _TIERS if by_tier.get(t, 0) < TIER_FLOOR},
        "imbalance_ratio": round(max(by_tier.values()) / max(min(by_tier.values()), 1), 2) if by_tier else 0,
    }


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Apply the S6 label policy and assemble the training set")
    p.add_argument("--judged-dir", default=str(repo / "evals/datasets/distilled/scale/judged"))
    p.add_argument("--s5-train", default=str(repo / "evals/datasets/distilled/train.jsonl"))
    p.add_argument("--eval-dir", default=str(repo / "evals/datasets"))
    p.add_argument("--out", default=str(repo / "evals/datasets/distilled/train_v2.jsonl"))
    p.add_argument("--no-policy", action="store_true",
                   help="keep raw judge-agreed labels (ablation baseline)")
    p.add_argument("--cap", type=int, default=TIER_CAP)
    a = p.parse_args(argv)
    report = assemble(Path(a.judged_dir), Path(a.s5_train), Path(a.eval_dir),
                      Path(a.out), apply_policy=not a.no_policy, cap=a.cap)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
