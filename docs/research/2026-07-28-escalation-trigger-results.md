# The escalation trigger — Rule A transfers, but the economics don't

**Date:** 2026-07-28 · **Pre-registration:** `2026-07-27-escalation-trigger-preregistration.md`

Rule A — agreement means trust — was the only round-2 claim that survived every instrument change.
This tested whether it transfers from a synthetic tool-call set I authored to **176 GitHub issues with
3-lab unanimous human labels**, using the two classifier arms already deployed in the router.

## Result: half the gate

| | measured | bar | |
|---|---|---|---|
| precision on covered | **0.917** | ≥ 0.90 | ✅ |
| coverage | **0.409** | ≥ 0.50 | ❌ |

**SHIP: False**, by the rule fixed before the number existed.

**The finding transfers.** Where the keyword matcher and the tuned `e2b_v3` agree, they are right
**91.7%** of the time against the hybrid's overall **76.1%** — agreement carries roughly **16 points**
of signal on a task and an instrument that have nothing to do with where the rule was discovered.
That is the strongest evidence this project has that Rule A is a property of ensembling rather than of
one eval set.

**The economics don't.** It escalates **59%** of traffic. A trigger that sends most requests to a
larger model is not a saving, and the pre-registration said so before this ran precisely so the good
half could not be reported as a pass.

## Where it is safe and where it is not

| tier | covered | precision |
|---|---|---|
| COMPLEX | 9 | **1.000** |
| CRITICAL | 18 | 0.944 |
| SIMPLE | 32 | 0.906 |
| MODERATE | 13 | **0.846** |

MODERATE is the weak cell, which is not a surprise — it has been this project's problem tier since the
keyword-precision diagnosis. CRITICAL at 0.944 is the one that matters operationally: the trigger
would ship one wrong CRITICAL in eighteen, and CRITICAL is the tier where a routing error costs most.

## Two harness bugs, both mine, both caught

**1. A bare `except` turned a bug into a data point.** The first run reported **coverage 0.0 across all
176 rows** and it looked like a finding — "the arms never agree." It was an `AttributeError`: I
guessed the classifier API (`classify` does not exist; the client exposes `complete`). My
`except Exception: return None` converted a programming error into 176 silent abstentions, which the
scorer faithfully reported.

**A measurement harness must let its own bugs crash it.** The arm now catches nothing, and a test
asserts that a raising arm propagates rather than scoring as an abstention.

**2. The model arm was nearly the keyword arm.** `classify_via_model` returns `(tier, source)` where
source may be `"keyword-fallback"` — the keyword classifier again under another name. Counting that as
a second opinion would have measured one arm against itself and inflated both coverage and agreement.
Fallbacks now return `None` and escalate.

## What would make it ship

Coverage is the binding constraint, and the pre-registered confound explains why it is *low* rather
than high: the arms are **not independent** — `classify_hybrid` consults the model only when keywords
default, so the two disagree structurally on exactly the rows where keywords are silent. A third,
genuinely independent arm (the 9B, or a differently-trained E2B) would let a majority vote cover the
rows where the first two split — which is what Rule B did on the tool-call set, resolving 3 of 4
contested rows.

That is the obvious next experiment and it is cheap. It is not run here because the pre-registration
fixed a two-arm rule, and switching to three arms after seeing a two-arm failure is choosing the
analysis that passes.
