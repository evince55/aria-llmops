"""A2's quality gate — blind A/B grading of the brevity clause.

The promotion gate scores TIER CLASSIFICATION and cannot answer this question.
A2 changes what the local build agent *writes*, so the thing to grade is answer
quality, and the brief was explicit: **kill the clause if the A/B shows a quality
regression.** That rule is encoded in `decide()` below and fixed before the run.

THREE METHOD RULES, each of which the result would be worthless without:

1. **Blind and position-randomised.** LLM judges have a well-documented position
   bias, so the two answers are shuffled per task and the verdict is unmapped
   afterwards. The prompt never names the arms — a judge that can tell which is
   the treatment is grading the hypothesis, not the answer. Observed placement
   counts are reported (`position_bias`) so a judge that simply always picks the
   first slot is visible rather than mistaken for a result.
2. **Independent grader.** opencode-go cloud labs only — never the model under
   test, never the local model, never zen. Same rule the eval-set labelling
   follows, for the same reason.
3. **The known bias runs AGAINST the treatment.** Judges tend to prefer longer,
   more thorough-looking answers, and the treatment is brevity. So a terse WIN is
   conservative evidence, while a terse LOSS is ambiguous between "worse" and
   "shorter". Stated here rather than discovered later.

Grading is on CORRECTNESS and COMPLETENESS rather than preference: for coding
tasks "does it do what was asked, and did it actually finish" is far less
squishy than "which do you like", and completeness is exactly where the verbose
arm was observed to fail (it hit the token cap on 16 of 24 tasks and, on at
least one, never produced the function at all).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.judge_labels import call_judge  # noqa: E402

# Declared BEFORE the run, matching the promotion gate's convention: a drop of
# more than this in either quality measure kills the clause regardless of how
# large the token or latency win is.
QUALITY_TOLERANCE = 0.05

DEFAULT_GRADERS = ("opencode-go/deepseek-v4-pro", "opencode-go/glm-5.2")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_VALID_WINNERS = {"A", "B", "TIE"}

PROMPT = """You are grading two candidate answers to the same software engineering request.

REQUEST:
{task}

--- ANSWER A ---
{a}

--- ANSWER B ---
{b}

Judge ONLY on these two things, and judge them independently for each answer:

* CORRECT — does the answer actually do what the request asked, without errors?
  An answer that is right but brief is CORRECT. An answer that is long,
  well-explained and wrong is NOT.
* COMPLETE — did it actually deliver the thing asked for? An answer that
  discusses approaches, or is cut off mid-way, or never gets to the deliverable,
  is NOT complete no matter how much it says.

Length is NOT a quality signal in either direction. Do not reward an answer for
being thorough-looking, and do not reward one for being short. A one-line answer
that fully satisfies the request scores exactly as well as a long one that does.

IF THE REQUEST CANNOT BE FULFILLED FROM WHAT IS GIVEN — for example it names a
file, function or snippet whose contents are not provided — then ASKING FOR THAT
MISSING INPUT IS THE CORRECT ANSWER. Mark such an answer CORRECT and COMPLETE.
An answer that instead INVENTS the missing file, fabricates its contents, or
guesses at code it was never shown is INCORRECT, however plausible or tidy it
looks. Confident fabrication is the worst outcome here, not the best.

Then pick the answer that better fulfils the request, or "TIE" if neither is
better.

Output STRICT JSON and NOTHING ELSE:
{{"winner": "A" | "B" | "TIE", "a_correct": true|false, "b_correct": true|false,
  "a_complete": true|false, "b_complete": true|false}}"""


def build_prompt(task: str, a: str, b: str) -> str:
    return PROMPT.format(task=task, a=a or "(no answer)", b=b or "(no answer)")


def parse_verdict(raw):
    """Longest-valid-object extraction; returns None rather than guessing.

    An unparseable grade must not be silently coerced into a verdict — that is
    the fallback-that-looks-like-an-answer failure this project keeps hitting.
    """
    text = _ANSI.sub("", raw or "")
    best = None
    for m in re.finditer(r"\{.*?\}", text, re.DOTALL):
        try:
            obj = json.loads(m.group(0))
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict) or obj.get("winner") not in _VALID_WINNERS:
            continue
        if not all(isinstance(obj.get(k), bool)
                   for k in ("a_correct", "b_correct", "a_complete", "b_complete")):
            continue
        best = obj
    return best


def grade(pairs, models=DEFAULT_GRADERS, judge=call_judge, seed: int = 0, cwd=None,
          checkpoint=None) -> dict:
    """Blind-grade `pairs` ({task, baseline, terse}). Majority vote across models."""
    for m in models:
        if not str(m).startswith("opencode-go/"):
            raise SystemExit(
                f"refusing grader {m!r}: A2's grader must be an independent opencode-go lab, "
                f"never the model under test or the local model")
    rng = random.Random(seed)
    rows, placements = [], Counter()
    for i, p in enumerate(pairs):
        terse_first = rng.random() < 0.5
        a, b = (p["terse"], p["baseline"]) if terse_first else (p["baseline"], p["terse"])
        shown = "A" if terse_first else "B"
        prompt = build_prompt(p["task"], a, b)
        votes = []
        for m in models:
            v = parse_verdict(judge(m, prompt, cwd))
            if v:
                votes.append(v)
        if not votes:
            print(f"  ! no gradable verdict for task {i}", file=sys.stderr)
            continue
        # Majority on the winner; per-answer flags by majority too.
        win = Counter(v["winner"] for v in votes).most_common(1)[0][0]
        placements[win if win != "TIE" else "TIE"] += 1

        def _maj(key):
            return sum(1 for v in votes if v[key]) * 2 > len(votes)

        a_correct, b_correct = _maj("a_correct"), _maj("b_correct")
        a_complete, b_complete = _maj("a_complete"), _maj("b_complete")
        winner_arm = ("tie" if win == "TIE"
                      else ("terse" if (win == shown) else "baseline"))
        # Written incrementally below: a 50-minute grading run was lost on
        # 2026-07-25 because results were only serialised at the end, and the run
        # had to be killed when the subscription hit its rolling limit.
        rows.append({
            "task": p["task"], "terse_shown_as": shown, "winner": win,
            "winner_arm": winner_arm, "n_votes": len(votes),
            "terse_correct": a_correct if terse_first else b_correct,
            "baseline_correct": b_correct if terse_first else a_correct,
            "terse_complete": a_complete if terse_first else b_complete,
            "baseline_complete": b_complete if terse_first else a_complete,
        })
        if checkpoint:  # survive a kill; see the note above
            try:
                Path(checkpoint).write_text(json.dumps({"rows": rows}, indent=2))
            except Exception:
                pass
    return {"rows": rows, "n": len(rows), "graders": list(models), "seed": seed,
            # Raw slot wins: a judge that always picks the first slot shows up
            # here as a lopsided count and invalidates the comparison.
            "position_bias": {"A": placements["A"], "B": placements["B"],
                              "TIE": placements["TIE"]}}


def tally(rows) -> dict:
    n = len(rows) or 1
    w = Counter(r["winner_arm"] for r in rows)
    return {
        "n": len(rows),
        "baseline_correct_rate": sum(r["baseline_correct"] for r in rows) / n,
        "terse_correct_rate": sum(r["terse_correct"] for r in rows) / n,
        "baseline_complete_rate": sum(r["baseline_complete"] for r in rows) / n,
        "terse_complete_rate": sum(r["terse_complete"] for r in rows) / n,
        "terse_wins": w["terse"], "baseline_wins": w["baseline"], "ties": w["tie"],
    }


def decide(t, tolerance: float = QUALITY_TOLERANCE) -> dict:
    """The pre-registered rule: keep the clause unless quality regressed.

    Deliberately one-sided. A2's token/latency win is already measured; this gate
    exists only to catch the clause making answers worse, so it asks whether
    terse FELL BEHIND, not whether it pulled ahead.
    """
    reasons = []
    dc = round(t["terse_correct_rate"] - t["baseline_correct_rate"], 4)
    dk = round(t["terse_complete_rate"] - t["baseline_complete_rate"], 4)
    if dc < -tolerance:
        reasons.append(f"correctness regressed {dc:+.3f} (tolerance {tolerance})")
    if dk < -tolerance:
        reasons.append(f"completeness regressed {dk:+.3f} (tolerance {tolerance})")
    return {"keep": not reasons, "reasons": reasons,
            "correctness_delta": dc, "completeness_delta": dk, "tolerance": tolerance}


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="A2 quality gate: blind A/B grade of the arms")
    p.add_argument("--baseline", default=str(repo / "logs/local_traffic_baseline.json"))
    p.add_argument("--terse", default=str(repo / "logs/local_traffic_terse.json"))
    p.add_argument("--models", default=",".join(DEFAULT_GRADERS))
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default=str(repo / "logs/a2_quality_gate.json"))
    a = p.parse_args(argv)

    base = {r["task"]: r for r in json.loads(Path(a.baseline).read_text())["rows"]}
    ters = {r["task"]: r for r in json.loads(Path(a.terse).read_text())["rows"]}
    shared = [t for t in base if t in ters]
    pairs = [{"task": t, "baseline": base[t]["output"], "terse": ters[t]["output"]}
             for t in shared]
    print(f"grading {len(pairs)} paired tasks with {a.models}", file=sys.stderr)

    res = grade(pairs, models=tuple(m for m in a.models.split(",") if m),
                seed=a.seed, cwd=repo, checkpoint=str(Path(a.out).with_suffix(".partial.json")))
    t = tally(res["rows"])
    verdict = decide(t)
    report = {**res, "tally": t, "verdict": verdict}
    Path(a.out).write_text(json.dumps(report, indent=2))
    print(json.dumps({"tally": t, "verdict": verdict,
                      "position_bias": res["position_bias"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
