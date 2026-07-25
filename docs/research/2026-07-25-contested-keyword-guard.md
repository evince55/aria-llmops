> ## ⚠️ UPDATE 2026-07-25 — THE GUARD WAS REMOVED. THE DIAGNOSIS BELOW STILL STANDS.
>
> Everything measured here was against an incumbent scoring MODERATE **0.417**. That incumbent
> turned out to be a **broken serving config** — `--repeat-penalty 0` on llama-server — not a
> weak model. With the flag fixed the live incumbent scores MODERATE **0.617**, so the guard's
> headline **+0.266** is really **+0.066** (0.617 → 0.683) bought for a **−0.062** SIMPLE
> regression the gate rejects. Not worth a branch in the classifier's hot path, so the code and
> its 13 tests are gone; `llmops.classify_detailed` carries a note pointing here.
>
> **What survives, and is still unfixed:** the root cause. Keyword rules match subject-matter
> **vocabulary** while the tier is set by the **work** — keywords still answer 29 of 60 MODERATE
> eval rows at 45% correct against the model's 74%. A future fix should raise keyword
> **precision** (stop firing on `performance` / `test` / `xss` when the task is ordinary feature
> work) rather than re-adding a blanket defer, which is what cost SIMPLE.
>
> **The tolerance-drift finding was independent of the guard** and shipped separately in
> [#42](https://github.com/evince55/aria-llmops/pull/42); it is unaffected and stays.
>
> Numbers below are left as originally measured — see
> `2026-07-25-live-gate-and-repeat-penalty.md` for what changed and why.

# The contested-row guard — and a flaw in the promotion rule it exposed

**Date:** 2026-07-25 · **Follows:** `2026-07-25-moderate-recall-arbitration.md`
**Verdict: PROMOTE against the S7 config, REJECT against the original incumbent.**
**Superseded — the guard was removed 2026-07-25 (see the banner above).**

The previous round established that MODERATE's deficit is keyword preemption, and that the
blunt fix (defer every keyword SIMPLE/COMPLEX prediction to the model) buys MODERATE at the
cost of SIMPLE. This round found the surgical version, and in measuring it turned up a
methodological problem worth more than the guard.

## Three data-fix hypotheses, all negative

The plan was a training-data fix on the SIMPLE/MODERATE boundary. It was abandoned on evidence:

| Hypothesis | Test | Result |
|---|---|---|
| **Length confound** — the training slices separate by length at 86% (`len<160 → SIMPLE` scores 86%/85%), so the model learns length | Are leaked SIMPLE rows longer? | **Refuted** — they are *shorter* (median 103 vs 136) |
| **Register gap** — human issues carry templates/signatures the training data lacks | Controlled probe: identical content, issue register added | **Weak** — 2 flips in 37 (5%) |
| **Prior mismatch** | training ratio vs test truth | **No** — 0.79 vs 0.82 |

Worth recording: the length confound is *real in the data* and would have been a plausible,
satisfying story. It just isn't what breaks on human text. Testing the prediction cost minutes;
acting on it would have cost a full generate → judge → tune → gate cycle.

## The instrument had to be rebuilt (again)

Policy selection needs held-out data with real power. The dev set from the previous round had
**28 SIMPLE rows** — each worth 3.6 points against a 3-row effect. It was expanded with the same
GitHub pipeline and fresh queries to **173 rows (SIMPLE 92, MODERATE 62)**, dropping
bot-generated template issues (`👤 Reported by: @…`) that the first pass let through.
COMPLEX/CRITICAL stay thin (7/12), so dev cannot check those tiers — the single test run does.

## The guard

`classify_detailed` returns `(tier, matched)`, where `matched` means *trust this, do not consult
a model*. CRITICAL/COMPLEX/SIMPLE win on a **single** keyword and are checked **before**
MODERATE, so one broad word outvotes multi-signal evidence:

- `Add portfolio performance calculation endpoint` → COMPLEX on `performance`, though `add` +
  `endpoint` both say MODERATE
- `Add a unit test for the checkout total` → SIMPLE on `test`, though `add` competes

The fix is not to delete rules but to **stop overclaiming**: when a single-keyword tier fires and
there is competing MODERATE evidence, report the tier but drop the confidence flag, so
`classify_hybrid` consults the model. Only *contested* rows defer — keywords stay authoritative
on uncontested ones, where they scored **32/32** on true SIMPLE. That is precisely why the blunt
policy regressed SIMPLE and this one does not. **CRITICAL is exempt**: over-routing security work
is the safe error, and its recall is already 0.93–0.97.

| Policy (DEV n=173) | acc | MODERATE | SIMPLE | Rows re-routed |
|---|---|---|---|---|
| defer all kw SIMPLE+COMPLEX | +0.035 | +0.145 | **−0.033** | 14 |
| guard, ≥2 MODERATE hits | +0.017 | +0.048 | +0.000 | 3 |
| **guard, ≥1 MODERATE hit** | **+0.029** | **+0.081** | **+0.000** | **5** |

Zero SIMPLE cost across 92 SIMPLE rows — a well-powered zero, unlike the blunt policy's small
dev cost that became −0.102 on test.

## Single-shot test (n=176, production `classify_hybrid` path)

| Config | acc | CRITICAL | COMPLEX | MODERATE | SIMPLE |
|---|---|---|---|---|---|
| original incumbent (kw+9B) | 0.705 | 0.931 | 0.763 | 0.417 | 0.878 |
| S7-promoted `e2b_v3_rescue` | 0.761 | 0.931 | 0.789 | 0.600 | 0.837 |
| **+ contested guard** | **0.784** | 0.931 | 0.789 | **0.683** | 0.816 |

- **vs S7-promoted config → PROMOTE**: acc +0.023, MODERATE +0.083, no tier regression.
- **vs original incumbent → REJECT**: SIMPLE −0.062, over the 0.05 tolerance.

MODERATE is **0.417 → 0.683 (+0.266)** against the incumbent — the tier this line of work
targeted — with CRITICAL and COMPLEX untouched.

## The finding that outlives the guard: tolerance drift

The rejection is not the guard's doing alone:

| Step | SIMPLE | Δ | Within 0.05? |
|---|---|---|---|
| original incumbent | 0.878 | — | — |
| S7 model swap (9B → e2b_v3) | 0.837 | −0.041 | yes |
| + contested guard | 0.816 | −0.021 | yes |
| **cumulative** | | **−0.062** | **no** |

**Each step passes the gate; the chain does not.** The rule compares a challenger to its
*immediate* incumbent, so a sequence of individually-tolerated regressions drifts arbitrarily far
from the original production baseline. The overrun here is 0.012 — about **0.6 of one row** — but
the mechanism is unbounded.

**Recommended rule change:** the gate should evaluate against **both** the immediate incumbent
*and* a pinned original baseline, with the tolerance applied to the cumulative delta. That is a
one-line addition to `promotion_gate.decide` and is left for its own increment rather than
being changed in the same run whose verdict it would alter.

## What shipped

- The guard, **off by default** (`LLMOPS_KEYWORD_GUARD=1` to enable). Production behaviour is
  unchanged unless opted in — same posture as S8's backend flag, and for the same reason: the
  pre-registered rule rejected it against the baseline production actually runs.
- 13 tests, including that the default is off and that CRITICAL keeps its exemption **with the
  guard on** (an earlier draft asserted the exemption with the guard off, where it proves nothing).
- 400 tests green.

## Next

1. **Buy back ~1 row of SIMPLE** and the guard clears the original incumbent outright. The
   cheapest candidates are the 9B→e2b_v3 SIMPLE losses on non-keyword rows, not the guard's rows.
2. Add cumulative-baseline checking to the gate (above).
3. S7's verdict is still PROVISIONAL — the incumbent row here is replayed, not measured.
