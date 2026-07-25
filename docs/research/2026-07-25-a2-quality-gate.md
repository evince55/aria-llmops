# A2's quality gate — the clause is KILLED, and the reason is useful

**Date:** 2026-07-25 · **Follows:** `2026-07-25-local-route-traffic.md`
**Verdict: KILL (pre-registered rule).** Correctness regressed −0.083 against a 0.05 tolerance.
Not shipped.

A2's token and latency arms were emphatic (−68% output tokens, −67% wall clock). The brief was
equally explicit: *"Kill it if the A/B shows quality regression in the eval gate."* It does.

## Method

The promotion gate scores tier CLASSIFICATION and cannot answer this — A2 changes what the build
agent *writes*. So: 24 paired tasks, both arms' actual answers, graded by two independent
opencode-go labs (`deepseek-v4-pro`, `glm-5.2`).

Three rules the result would be worthless without:

* **Blind and position-randomised.** Arms shuffled per task, verdict unmapped after; the prompt
  never names them. Observed placement: **A 7 · B 6 · TIE 11** — no position bias, so the
  blinding held.
* **Independent grader** — never the model under test, never the local model.
* **The known bias runs AGAINST the treatment.** LLM judges prefer longer answers and the
  treatment is brevity, so a terse win is conservative evidence and a terse loss is ambiguous
  between *worse* and *shorter*. The prompt grades correctness/completeness explicitly and states
  that length is not a quality signal, but the bias cannot be assumed away.

## Result

| measure | baseline | terse | Δ |
|---|---|---|---|
| **correct** | 0.708 | 0.625 | **−0.083** ← kills it |
| complete | 0.708 | 0.833 | **+0.125** |
| head-to-head wins | 5 | **8** | (11 ties) |

The verdict is not clean, and pretending otherwise would be dishonest: **terse wins more
head-to-head and is materially more complete.** But the pre-registered rule is one-sided on
correctness for a reason — a faster, tidier wrong answer is worse than a slow right one — and
correctness regressed past tolerance. The clause does not ship.

## The regression is concentrated, which is the actually useful finding

| terse LOST where baseline was right (6) | terse WON where baseline was wrong (4) |
|---|---|
| ISO-8601 timestamp parser | add a docstring |
| SQL: 10 most recent orders per customer | shell one-liner → script with error handling |
| Makefile `test` target with coverage floor | cache-control header on a FastAPI response |
| FastAPI pagination (limit/offset) | refactor a 40-line validator into helpers |
| debounce decorator, configurable delay | |
| deep-merge two nested dicts without mutating | |

The losses are **implementation tasks with edge cases**; the wins are **small/edit tasks**.
"Answer first… stop when the answer is complete" appears to make the model under-deliver exactly
where completeness *means* handling cases — a deep-merge that doesn't recurse properly, a
timestamp parser that skips timezones.

**This maps onto the router's own tiers.** The natural refinement is a **tier-conditional**
clause: brevity on SIMPLE-tier work, full output on MODERATE and above. That is a different
experiment, not a tweak to this one, and it should be pre-registered and run the same way.

Stated as strength: this is **6 tasks versus 4**, suggestive of a pattern, not proof of one.

## An honest weakness in this gate

At n=24 one task is **0.042**, so the 0.05 tolerance is **1.2 tasks** — the instrument barely
resolves its own rule, and the kill margin is literally two tasks. A run at this size can
distinguish "much worse" from "much better" but not the middle, which is where this landed. Any
re-run of the tier-conditional variant should use materially more tasks before the verdict is
treated as settled.

## What stands regardless

The token and latency measurements are unaffected — those were counted, not judged. On a
throughput-bound local route output tokens *are* wall clock, and the clause cuts both by roughly
two thirds. That win is real; it just cannot be bought at the price of correctness.

`evals/grade_ab.py` (+18 tests) is reusable for the tier-conditional follow-up.
