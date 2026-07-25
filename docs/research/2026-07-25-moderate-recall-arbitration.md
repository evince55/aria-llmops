# MODERATE recall: the cause is keyword preemption, and the cure is one tier away

**Date:** 2026-07-25 · **Follows:** `2026-07-22-s7-complex-regen-gate-results.md`,
`2026-07-25-s8-deploy-and-degradation.md`
**Verdict: REJECT** (pre-registered gate rule). Best accuracy the project has measured —
blocked on a five-row SIMPLE regression. Nothing shipped.

MODERATE was the weakest tier in production (incumbent recall **0.417** — of 60 rows it gets
25, scattering 17 to SIMPLE and 12 to COMPLEX). The obvious plan was "generate more MODERATE
training data." The investigation says that plan was wrong.

## Root cause: keywords preempt rows they get wrong

The gate data already contained the signal — the *only* difference between these two configs is
the keyword layer running first:

| Config | MODERATE recall |
|---|---|
| `e2b_v3_standalone` | 0.783 |
| `e2b_v3_rescue` (keyword-first) | 0.600 |

Path attribution on the 60 MODERATE rows makes it explicit:

| Path | Answered | Correct |
|---|---|---|
| Keywords (preempt) | 29 | **13 (45%)** |
| Model (rescue) | 31 | **23 (74%)** |

**13 rows are keyword-wrong-but-model-right**; only 3 are both-wrong. Across all 103 preempted
rows the model is more accurate than the keywords (82% vs 75%) — the layer whose job is to be
free and fast is also being asked to be right, and on this tier it is not.

**The mechanism is structural.** Keyword rules fire on *subject-matter vocabulary* while the
tier is set by *the work being done*:

| Task | Keyword says | Because | Actually |
|---|---|---|---|
| `[REFACTOR] Rename variables to better comprehend` | COMPLEX | "refactor" | MODERATE |
| `Add logout endpoint… wipe the JWT token` | CRITICAL | auth vocabulary | MODERATE |
| `Implement XSS Scanner` | CRITICAL | "XSS" | MODERATE |
| `Add portfolio performance calculation endpoint` | COMPLEX | "performance" | MODERATE |

This is the same error the project's own `_CLASSIFY_PROMPT` warns judges about — *"a mere
mention of a sensitive DOMAIN is not enough"* — and it is not fixable by adding rules. A
substring matcher cannot read intent. **More MODERATE training data would not have touched it.**

## The instrument problem (and why the first answer was wrong)

Choosing an arbitration policy requires held-out data — fitting it on the 176-row test set and
then scoring on that set is the contamination this project has been careful about.

The first dev set (101 rows assembled from the older operator harvest) produced a clean negative
result: *every* policy that lifted MODERATE regressed SIMPLE past tolerance. **That result was an
artifact.** Its SIMPLE tier is polluted with agent-ops directives — `"merge pr 17"`,
`"check that the backend has been updated"`, `"Set up ssh so you can enter my other machine"` —
which the 3-lab validity gate passes **3/3 unanimously**, and rightly: they *are* tasks. They are
just a category the tier rubric does not model (trivial to execute, multi-step to perform), so
the human label says SIMPLE and the model says MODERATE and neither is clearly wrong.

**Lesson, and it rhymes with the last one:** the operator harvest could not produce a balanced
COMPLEX/CRITICAL tier (that is why the GitHub set exists) and it cannot produce a *clean SIMPLE*
tier either. A dev instrument must come from the same source and register as the test
instrument. Cleaning could not fix it — the repo's deterministic filters caught 0 of these rows
(they were harvested through those filters), and the model gate kept them all.

So a second instrument was built the right way: **78 unanimous rows from fresh GitHub issues**
(75 distinct repos, different queries from the test harvest, 0 exact / 0 fuzzy overlap with the
176 and with `train_v3`), 64.5% unanimity vs the test set's 62.5%.

## Policy selection — on dev, once

Candidate policies differ only in which keyword *predictions* are allowed to preempt.

| Policy (DEV n=78) | acc | MODERATE | Regressions >0.05 |
|---|---|---|---|
| P0 current | 0.654 | 0.455 | — |
| P1 defer SIMPLE | +0.038 | +0.091 | none |
| **P2 defer SIMPLE+COMPLEX** | **+0.064** | **+0.152** | **none** |
| P3 keywords keep CRITICAL only | +0.064 | +0.152 | none |
| P4 standalone | +0.090 | +0.212 | CRITICAL −0.182 |

P2 and P3 tie; **P2** was committed before touching test, on two prior grounds — it changes
fewer routing decisions, and it preserves the free keyword fast-path on one more bucket.

## Single-shot test (n=176)

| Config | acc | CRITICAL | COMPLEX | MODERATE | SIMPLE |
|---|---|---|---|---|---|
| incumbent (kw+9B, replayed) | 0.705 | 0.931 | 0.763 | 0.417 | 0.878 |
| `e2b_v3_rescue` (shipped) | 0.761 | 0.931 | 0.789 | 0.600 | 0.837 |
| **+ P2 arbitration** | **0.807** | **0.966** | 0.789 | **0.767** | **0.776** |

**MODERATE 0.417 → 0.767 (+0.350 over the incumbent)** — the problem this increment set out to
solve, solved. CRITICAL *improves* to 0.966, the highest recorded. 0.807 is the best accuracy the
project has measured.

**And the gate REJECTS it: SIMPLE −0.102 vs the incumbent, past the 0.05 tolerance.**

The rule was fixed before the run and is applied as written. Nothing ships.

## Exactly why, in five rows

Of the 61 rows P2 newly defers to the model:

| | n | Keywords right | Model right |
|---|---|---|---|
| true MODERATE | 13 | **0** | **10** |
| true SIMPLE | 32 | **32** | 29 |

It trades **3 SIMPLE for 10 MODERATE** on the deferred set (net 44 → 52 correct). The whole
rejection is the model's **SIMPLE → MODERATE over-prediction** (leak: 10/49 on test, 10/24 on the
old dev set) landing on a tier small enough that five rows exceed the tolerance.

## The next increment is now precisely specified

The remaining defect is not routing and not MODERATE — it is the classifier's **SIMPLE/MODERATE
boundary**, and it is a data problem of exactly the shape S7 fixed for COMPLEX:

1. Sharpen the SIMPLE and MODERATE training slices so the boundary sits where human text puts
   it (S7's COMPLEX fix moved a boundary by changing what the generator withholds; the analogue
   here is making SIMPLE examples stop looking like small features).
2. Re-tune, then **re-apply P2** — it needs only ~5 rows of SIMPLE recall to pass, and it already
   clears accuracy by +0.10 and improves CRITICAL.

Also still open: the S7 verdict remains PROVISIONAL (no coherent 9B endpoint), so the incumbent
row above is replayed, not measured.

## Artifacts

- Dev instrument: `evals/datasets/dev_gh_provisional.jsonl` (78 rows) +
  `dev_gh_review_queue.jsonl` (43 contested). Data files are gitignored per project convention;
  the harvest queries are recorded in this document and the pipeline is
  `harvest_github_tasks.py` → `label_eval_set.py`, unchanged.
- No production code changed. The policy is documented, not shipped, because the gate rejected it.
