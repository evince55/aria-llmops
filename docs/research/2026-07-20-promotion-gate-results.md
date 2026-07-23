# The promotion gate — first real promote/reject decision

**Date:** 2026-07-20 · **Instrument:** `labeled_tasks_github.jsonl` (176 rows, tier-balanced,
human-written, quarantine-clean) · **Code:** `evals/promotion_gate.py`
**Verdict: REJECT both challengers** — not because the tuned SLM is worse (it wins on
aggregate accuracy) but because it regresses COMPLEX by 18 points, and the no-regression
rule holds.

This is the decision S5 and S6 built toward and never got to make: the missing half of the
NVIDIA S1–S6 reproduction, which is not "we fine-tuned an SLM" but "we replaced calls at
equal quality — or declined to, on evidence."

## The rule, encoded before the run

```
PROMOTE iff challenger_accuracy >= incumbent_accuracy
        AND no tier recall regresses by more than 0.05
```

The 0.05 tolerance was declared in code before any number was seen; per-tier recall on ~29
CRITICAL rows moves in ~3-point steps, so a literal zero rule rejects on noise. Encoding it
first is what stops a borderline result being rationalised into a promotion afterwards.

## Results (n=176)

| Config | Accuracy | CRITICAL | COMPLEX | MODERATE | SIMPLE |
|---|---|---|---|---|---|
| **incumbent** — keyword + 9B rescue | 0.705 | 0.931 | **0.763** | 0.417 | **0.878** |
| **e2b_standalone** — tuned 3.2 GB SLM | **0.744** | 0.931 | 0.579 | **0.750** | 0.755 |
| **e2b_rescue** — keyword + tuned E2B | 0.710 | 0.931 | 0.579 | 0.583 | 0.837 |

| Challenger | Accuracy Δ | Tier regressions | Verdict |
|---|---|---|---|
| e2b_standalone | **+0.039** | COMPLEX −0.184, SIMPLE −0.123 | **REJECT** |
| e2b_rescue | +0.005 | COMPLEX −0.184 | **REJECT** |

## What the powered instrument revealed

**The 0.810 tie was undersampling, not a tie.** On the 42-row union both configurations
scored 0.810 and the decision was unresolvable. At n=176 the picture is not a tie at all —
it is a *trade*: the tuned SLM is **+33 points on MODERATE** (0.750 vs 0.417) and
**−18 on COMPLEX** (0.579 vs 0.763). Aggregate accuracy hid two large, opposing tier
movements. This is precisely why the eval set had to be rebuilt before the gate could mean
anything.

**CRITICAL is identical at 0.931 across all three configurations** (27/29). The tier where a
routing error is most expensive is the one where the cheap 3.2 GB model fully matches a
5.8 GB hybrid — the strongest single piece of evidence for the SLM thesis in this project.

**The incumbent has a MODERATE problem nobody had measured.** Recall 0.417: of 60 MODERATE
rows it gets 25, scattering 17 to SIMPLE and 12 to COMPLEX. The 42-row set showed 0.583 and
hid the severity. The production router is weakest on its most common tier.

## Root cause of the COMPLEX regression — predicted, unfixed, now measured

The confusion matrix names it. Of 38 COMPLEX rows the tuned E2B mislabels 16, and **11 of
those go to MODERATE**:

```
e2b_standalone   COMPLEX (n=38) -> COMPLEX 22, MODERATE 11, CRITICAL 5
```

That is exactly the defect S6's own audit predicted and explained
(`2026-07-18-s6-dataset-scale-results.md`): the COMPLEX generator emitted *prescribed-fix
tickets* — tasks naming both cause and remedy ("N+1 → switch to `selectinload`") — which read
as MODERATE wiring dressed in concurrency vocabulary. Only 66/160 generated COMPLEX rows
survived judging as COMPLEX. The audit's recommendation was explicit: **have the COMPLEX
generator withhold the diagnosis and the fix.** It was documented and never applied.

So the model was trained on COMPLEX examples that look like MODERATE work, and it learned
exactly that. The gate has now measured the downstream cost of an unfixed data defect:
**−18 points of COMPLEX recall, which is the sole reason `e2b_rescue` fails promotion.**

## Next increment (concrete, evidence-backed)

1. **Regenerate the COMPLEX training slice with the fix S6 specified** — withhold the
   diagnosis and remedy; keep tasks that preserve genuine uncertainty ("I suspect… root-cause
   it"). Budget ~2.4× over-generation for that tier, per the S6 yield measurement.
2. **Re-tune E2B and re-run this gate.** `e2b_rescue` needs only +18 points of COMPLEX recall
   to pass; it already clears accuracy and ties CRITICAL. This is the narrowest, best-specified
   gap the flywheel has ever had.
3. **Independently, fix the incumbent's MODERATE recall (0.417)** — that is a production
   weakness regardless of which classifier wins.

## Honest framing

A rejection is a real result, not a failed one: the loop ran end to end, the rule was fixed in
advance, the verdict names the failing tier, and the failure traces to a specific, already-
diagnosed data defect with a specific fix. That is the S1–S6 conversion algorithm behaving as
an instrument rather than a demo — which is the reproduction claim worth making.
