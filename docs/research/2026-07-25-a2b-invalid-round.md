# A2b — INVALID ROUND. The gate rewarded confabulation.

**Date:** 2026-07-25 · **Follows:** `2026-07-25-a2-quality-gate.md`
**Verdict: WITHDRAWN.** The run produced a clean-looking `keep: true` with a **+0.245**
correctness improvement. It is an artefact and is not reported as a result.

## What was being tested

A2 killed the blanket brevity clause on correctness (0.708 → 0.625) but the regression was
concentrated: terse lost implementation tasks and won small/edit ones. A2b tested the
tier-conditional form — clause on SIMPLE-tier work only. Because MODERATE+ is byte-identical in
both arms, the experiment reduces to one question: *does the clause hurt SIMPLE-tier tasks?*

Pool: **53 tasks the router itself classifies SIMPLE** (membership is `classify_detailed`'s call,
not a hand label, because that is what would gate the clause in production). At n=53 the 0.05
tolerance is 2.7 tasks, more than double A2's 1.2 — the power problem A2 flagged was fixed.

## The efficiency arms (these stand)

| | baseline | terse | Δ |
|---|---|---|---|
| output tokens | 18,352 | 3,651 | **−80%** |
| wall clock | 641 s | 141.5 s | **−78%** (4.5×) |
| mean per task | 12.09 s | 2.67 s | −78% |
| truncated at the cap | 4 | **0** | — |

Larger than A2's mixed-set −68%/−67%, which fits: SIMPLE tasks carry the most preamble relative
to content. These were counted, not judged, and are unaffected by what follows.

## Why the quality result is void

The gate reported correctness 0.264 → 0.509 and completeness 0.226 → 0.528. Those absolute rates
are implausibly low for *simple* work, which is what prompted the check. The outputs explain it:

> **Task:** *"Fix the typo 'recieve' in the comment above parse_response in `api/client.py`."*
> **baseline:** *"Please provide the comment you are referring to in `api/client.py` so I can fix
> the typo…"* → graded **INCORRECT**
> **terse:** ` ```python # receive ``` ` → graded **CORRECT**

The harness is a bare completion endpoint with **no file access**. The baseline asked for the file
it could not see — the correct response — and was marked wrong. Terse fabricated, and was marked
right. On another task terse invented an entire `uploader.py` and scored correct for it.

Measured, not asserted:

* **34 of 53 tasks (64%)** name a file the model was never shown.
* The baseline asked for the missing file on **31 of 53** tasks.
* **All 31** were graded incorrect for asking — **0.585 of the set**, which dwarfs the reported
  +0.245.

So the experiment measured *willingness to bluff on unanswerable tasks*, not answer quality. The
apparent win belongs to whichever arm confabulates more readily.

## Two defects, both now guarded

1. **Rubric.** `grade_ab.py` now instructs the judge that when a request cannot be fulfilled from
   what is given, **asking for the missing input IS the correct answer**, and inventing the file
   is incorrect however tidy it looks. Pinned by tests. This was wrong independently of the pool
   and would have quietly rewarded fabrication in any future A/B.
2. **Pool.** `simple_tier_tasks.needs_file_context()` flags tasks naming unseen files; a test
   records that **67%** of the current pool trips it, so the defect cannot silently reappear.
   The tasks are *not* deleted — they are what the router's SIMPLE tier actually contains.

## The structural finding

**The router's SIMPLE tier is mostly file-edit work, and a file-blind harness cannot evaluate
it.** That is not a pool-selection mistake to be tuned away; it is a mismatch between the
population and the instrument. A valid A2b requires **supplying context** — a synthetic file
snippet per task — so the work is actually doable. Restricting the pool to self-contained tasks
would run, but would no longer be the population a tier-conditional clause gates.

## Status

- A2b: **open, not answered.** The verdict is withdrawn, not recorded.
- A2's original KILL stands unchanged — it used self-contained tasks and is unaffected.
- The efficiency numbers above stand.
