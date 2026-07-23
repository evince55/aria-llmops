# S6 — E2B re-tuned on balanced data, measured against the incumbent

**Date:** 2026-07-18 · **Follows:** `2026-07-18-s6-dataset-scale-results.md`, `2026-07-17-s5-thin-slice-results.md`
**Verdict: DO NOT PROMOTE.** The tuned SLM improved substantially and now beats the 9B
outright, but it does not beat the *hybrid* router that is actually in production.

## What ran

`train_v2.jsonl` (677 examples, imbalance 2.06:1) → `mlx_lm lora` QLoRA on the 4-bit E2B base,
S5's exact recipe (LoRA rank 8, 8 layers, batch 4, LR 1e-4, masked prompt) at **600 iters**
(~4.2 epochs), checkpoints every 100 → eval on the quarantined **42-example human union**.

Training converged cleanly and **did not diverge** — the E4B failure mode from S5 did not recur:

| Iter | 1 | 50 | 150 | 250 | 400 | 500 | 600 |
|---|---|---|---|---|---|---|---|
| Val loss | 11.438 | 0.146 | 0.075 | 0.048 | 0.049 | **0.020** | 0.066 |

## Results — 42-example human union

| Classifier | Size | Accuracy | CRITICAL | COMPLEX | MODERATE | SIMPLE |
|---|---|---|---|---|---|---|
| **Incumbent `classify_hybrid`** (keyword + 9B rescue) | 5.8 GB | **0.810** | 1.00 | 0.769 | 0.583 | 1.00 |
| **Hybrid with E2B-v2 as rescue** | 3.2 GB | **0.810** | 1.00 | 0.615 | 0.833 | 0.90 |
| **E2B-v2 tuned, standalone** | 3.2 GB | **0.738** | 0.714 | 0.538 | 0.833 | 0.90 |
| 9B alone (production path) | 5.8 GB | 0.667 | — | — | — | — |
| E2B-v2 best-*val* checkpoint (iter 500) | 3.2 GB | 0.667 | 0.857 | 0.385 | 0.667 | 0.90 |
| S5 E2B-tuned (182 examples) | 3.2 GB | 0.619 | 1.00 | 0.23 | 0.58 | 0.90 |

per-tier = recall. n=42, so **one example is 2.4 points** — read small gaps as noise.

## What the data scaling bought

**COMPLEX recall 0.23 → 0.538**, tracking its training count 13 → 119. That was S6's entire
premise and it held. Standalone accuracy moved **0.619 → 0.738 (+11.9 pts)**, and the tuned
3.2 GB E2B now **beats the 5.8 GB 9B outright (0.738 vs 0.667)**.

CRITICAL recall fell 1.00 → 0.714, but support is 7 — two examples. Not a conclusion.

## Why it still fails the gate

**The incumbent is not the 9B; it is `classify_hybrid`** — keyword-first, consulting the 9B
only when the keyword pass defaults. On this eval 25/42 tasks never reach the model at all.
The keyword layer is doing the heavy lifting exactly where it matters: it takes CRITICAL to
1.00 recall, while every standalone model tested sits at 0.71–0.86.

So the honest framing is not "SLM vs 9B" but "SLM vs keyword rules + 9B". The tuned E2B beats
the model half of that pair and loses to the pair.

## The interesting result: same accuracy, 55% of the memory

Dropping E2B-v2 in as the **rescue model inside the hybrid**, keeping keyword-first, scores
**0.810 — an exact tie with the incumbent** at 3.2 GB instead of 5.8 GB. It does not dominate:
it trades MODERATE (0.583 → 0.833) for COMPLEX (0.769 → 0.615) and SIMPLE (1.00 → 0.90).

Under the standing rule — promote only with **no tier regression** — a tie with two regressed
tiers is not a promotion. But "matches the incumbent at 55% of the footprint" is the most
promising configuration found so far, and it is the one worth resolving next.

## Three methodological findings

**1. Validation loss did not predict eval accuracy.** The best-*val* checkpoint (iter 500,
val 0.020) scored **0.667**, worse than the final checkpoint (val 0.066) at **0.738**. The
validation split is drawn from the same synthetic distribution as training; the eval union is
human-written. Select checkpoints on the target distribution, not on in-distribution val loss.

**2. `classify_finetuned.py` cannot evaluate a reasoning model.** Run through the MLX harness,
the 9B scored **0.286 — the exact always-predict-MODERATE floor** (MODERATE recall 1.00, every
other tier 0.00). Cause: it emits `Thinking Process:` before answering, the 8-token budget
captures only the preamble, and `map_tier` falls back to MODERATE on unparseable output.
Raising the budget is worse, not better — `map_tier` substring-matches and picks "CRITICAL"
out of the model's own restatement of the rubric, scoring everything CRITICAL. Temperature was
tested and ruled out (greedy gives the same preamble); the difference is prompt/template
construction versus `mlx_lm.server`'s chat endpoint, where the same model parsed 42/42.

This is the **third** instance of this bug class in this project (Bonsai's false 0/4, the
outcome-grader phantom failures). A model scoring exactly at a degenerate floor should always
be treated as a harness artifact until the raw output is inspected.

**3. `labeled_tasks_balanced.jsonl` is not a human benchmark.** Its `source` field is
`35b-gen+audited` — model-generated. E2B-v2 scores **0.92** on it versus 0.738 on the human
union. Both are quarantined (0 exact and 0 fuzzy overlap), so this is not contamination; it is
distribution match — that eval set and the S6 training data are both model-written (mean 133
and 236 chars) while the human union is terser (110 chars, e.g. "fix a typo in the README").
**The 0.738 is the honest number.** The 0.92 should never be quoted as headline accuracy.

## What actually blocks a promotion decision

**The eval set, not the model.** At n=42 a tie is 0 examples and each tier holds 7–13 items, so
"0.810 vs 0.810 with two tiers regressed" cannot be resolved — the per-tier gaps are one or two
items. Every remaining question needs a larger *human-labeled* eval set; generating more with a
model reproduces the style-match problem in finding 3.

## Next

1. **Build a larger human-labeled eval set** (target ≥150, tier-balanced). Highest-value work
   available; nothing else can be concluded without it.
2. **Then resolve E2B-v2-as-rescue.** Matching the incumbent at 55% of memory is worth
   confirming or killing on an eval with the power to do so.
3. **COMPLEX remains the weakest tier** (0.538 standalone / 0.615 as rescue). Before generating
   more data, apply the generator fix from the dataset write-up: COMPLEX tasks must *withhold*
   the diagnosis and fix, since prescribed-fix tickets read as MODERATE.
4. E4B stays out — it never converged in S5 and nothing here changes that.
