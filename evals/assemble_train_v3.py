"""Assemble train_v3: train_v2 with ONLY the COMPLEX slice swapped.

The promotion gate measured the cost of the defective COMPLEX slice at -18
points of recall. This builds the dataset that tests the audit's fix.

DESIGN: the COMPLEX COUNT IS HELD AT 119, exactly what train_v2 had.

That is the whole point. The regeneration yields far more usable rows than 119,
and using all of them would change the slice's SIZE and its TEXT at the same
time -- so a recall improvement could be credited to either, and the audit's
claim ("prescribed-fix tickets read as MODERATE; withhold the diagnosis") would
go untested. Holding count constant isolates text quality as the sole variable.

The surplus is written alongside as `complex_v3_surplus.jsonl` for a follow-up
scale ablation, which is a DIFFERENT question (does more COMPLEX data help?)
and deserves its own run rather than being smuggled into this one.

Sampling is round-robin across the four domains so the swap does not also
change the domain mix.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path


def load(path: Path) -> list:
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def stratified_take(rows: list, n: int, key: str = "domain") -> list:
    """Round-robin across groups so the sample keeps the domain mix rather than
    front-loading whichever domain generated fastest."""
    buckets: dict = collections.defaultdict(list)
    for r in rows:
        buckets[r.get(key, "-")].append(r)
    order = sorted(buckets)
    out: list = []
    i = 0
    while len(out) < n and any(buckets[g] for g in order):
        g = order[i % len(order)]
        if buckets[g]:
            out.append(buckets[g].pop(0))
        i += 1
    return out


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Assemble train_v3 (COMPLEX slice swap)")
    p.add_argument("--base", default=str(repo / "evals/datasets/distilled/train_v2.jsonl"))
    p.add_argument("--complex", default=str(repo / "evals/datasets/distilled/complex_v3_agreed.jsonl"))
    p.add_argument("--eval-set", default=str(repo / "evals/datasets/labeled_tasks_github.jsonl"))
    p.add_argument("--out", default=str(repo / "evals/datasets/distilled/train_v3.jsonl"))
    p.add_argument("--surplus", default=str(repo / "evals/datasets/distilled/complex_v3_surplus.jsonl"))
    a = p.parse_args(argv)

    base = load(Path(a.base))
    fresh = [r for r in load(Path(a.complex)) if r.get("tier") == "COMPLEX"]
    old_complex = [r for r in base if r.get("tier") == "COMPLEX"]
    keep = [r for r in base if r.get("tier") != "COMPLEX"]
    target = len(old_complex)

    # Quarantine is re-asserted here, not assumed: this file is what actually
    # reaches training, and the eval set is the instrument that judges it.
    evalset = {_norm(r["task"]) for r in load(Path(a.eval_set))}
    leaked = [r for r in fresh if _norm(r["task"]) in evalset]
    if leaked:
        raise SystemExit(f"QUARANTINE BREACH: {len(leaked)} row(s) also in the eval set")

    if len(fresh) < target:
        print(f"WARNING: only {len(fresh)} agreed COMPLEX rows for a target of {target}; "
              f"the slice will be SMALLER than v2 and size is no longer controlled.",
              file=sys.stderr)

    chosen = stratified_take(fresh, target)
    chosen_keys = {_norm(r["task"]) for r in chosen}
    surplus = [r for r in fresh if _norm(r["task"]) not in chosen_keys]

    rows = keep + [{"task": r["task"], "tier": "COMPLEX", "source": "synthetic-v3-complex"}
                   for r in chosen]

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    with Path(a.surplus).open("w", encoding="utf-8") as fh:
        for r in surplus:
            fh.write(json.dumps(r) + "\n")

    print(json.dumps({
        "n": len(rows),
        "tiers": dict(collections.Counter(r["tier"] for r in rows)),
        "complex_swapped": {"out": len(old_complex), "in": len(chosen),
                            "held_constant": len(chosen) == target},
        "complex_domains": dict(collections.Counter(r.get("domain", "-") for r in chosen)),
        "surplus_held_back": len(surplus),
        "quarantine_exact_overlap": 0,
        "out_path": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
