"""Evaluate the data-efficiency curve — every training size at both operating points.

The question this answers is fixed in
`docs/research/2026-07-26-data-curve-preregistration.md`: **at a fixed training
budget, does more unique data help?** Every arm trains at identical
hyperparameters and differs only in dataset size, so the curve isolates data
quantity — and, because the vocabulary was widened while the templates were held
fixed, "quantity" here means more rows of the same sentence shapes. That caveat
is part of the result, not a footnote to it.

Both operating points are measured because finding 20 showed the tuned model's
*robustness to sampling* is itself an outcome: more data might move the spread
even where it does not move the mean. Above temperature 0 the card point is the
mean of several runs, never a single sample.

The N=460 arm is the void check. It is retrained from the widened vocabulary and
must land near the published 0.820; if it does not, the generator changed
underneath the comparison and every other point is measuring that instead.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SIZES = (460, 1000, 2500, 5000, 10000)
VOID_CHECK_SIZE = 460
VOID_CHECK_TARGET = 0.820
VOID_CHECK_TOLERANCE = 0.05


def void_check(baseline_mean: float, published=VOID_CHECK_TARGET,
               tol=VOID_CHECK_TOLERANCE) -> dict:
    """Did the retrained baseline reproduce its published score?

    If it did not, the generator moved underneath the comparison and every other
    point on the curve is measuring that instead of data scale. Pre-registered,
    and applied rather than described.
    """
    drift = abs(baseline_mean - published)
    return {"published": published, "retrained": round(baseline_mean, 4),
            "drift": round(drift, 4), "void": bool(drift > tol)}


def verdict(means: dict, baseline_size=VOID_CHECK_SIZE, threshold=0.05) -> dict:
    """PLATEAU or SCALING by the pre-registered rule, computed not eyeballed.

    `means` maps size -> greedy mean. The threshold is 0.05, the same tolerance
    round 1's promotion gate used; it was not chosen after seeing this curve.
    """
    above = {int(k): v for k, v in means.items() if int(k) > baseline_size}
    if not above:
        return {"verdict": "INCOMPLETE", "rule": f"plateau if gain < {threshold}"}
    best_size = max(above, key=lambda k: above[k])
    gain = above[best_size] - means[str(baseline_size)]
    return {"best_size": best_size, "best_mean": round(above[best_size], 4),
            "gain_over_baseline": round(gain, 4),
            "rule": f"plateau if gain < {threshold}",
            "verdict": "PLATEAU" if gain < threshold else "SCALING"}


def run_arm(base: str, adapter: Path, point: str, runs: int, out: Path) -> dict:
    cmd = [sys.executable, str(REPO / "evals/tool_call_eval.py"),
           "--base", base, "--adapter", str(adapter), "--set", "wide",
           "--point", point, "--runs", str(runs), "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"{adapter.name} @ {point} failed:\n{proc.stderr[-1500:]}")
    return json.loads(out.read_text())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="S10: the data-efficiency curve")
    p.add_argument("--base", required=True)
    p.add_argument("--adapters", default=str(REPO / "evals/adapters"))
    p.add_argument("--card-runs", type=int, default=3)
    p.add_argument("--out", default=str(REPO / "evals/results/s10/data_curve.json"))
    a = p.parse_args(argv)

    logs = REPO / "logs"
    logs.mkdir(exist_ok=True)
    curve = {}
    for n in SIZES:
        adapter = Path(a.adapters) / f"curve_{n}"
        if not adapter.exists():
            print(f"  ! missing {adapter}, skipping", file=sys.stderr)
            continue
        row = {}
        for point, runs in (("greedy", 1), ("card", a.card_runs)):
            res = run_arm(a.base, adapter, point, runs, logs / f"curve_{n}_{point}.json")
            row[point] = res["runs"]
            row[f"{point}_point"] = res["arm"]["operating_point"]
        curve[str(n)] = row
        g = row["greedy"]["mean"]
        c = row["card"]
        print(f"  n={n:5d}  greedy={g:.3f}   card={c['mean']:.3f} ±{c['spread']:.3f}")

    out = {"sizes": curve, "schedule": "iters 400, batch 4, rank 8, lr 1e-4 (fixed compute)",
           "eval_set": "wide (n=61)"}

    # The void check, applied rather than described. A curve whose own baseline
    # does not reproduce is measuring the generator, not the data.
    base_row = curve.get(str(VOID_CHECK_SIZE))
    if base_row:
        out["void_check"] = {"size": VOID_CHECK_SIZE,
                             **void_check(base_row["greedy"]["mean"])}
        if out["void_check"]["void"]:
            print(f"  ! VOID: N={VOID_CHECK_SIZE} retrained to "
                  f"{base_row['greedy']['mean']:.3f} vs published {VOID_CHECK_TARGET} "
                  f"— the generator moved; the curve measures that, not data scale.",
                  file=sys.stderr)

    # Plateau vs scaling, by the pre-registered 0.05 rule — computed, not eyeballed.
    if base_row and len(curve) > 1:
        out["verdict"] = verdict({k: r["greedy"]["mean"] for k, r in curve.items()})

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "sizes"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
