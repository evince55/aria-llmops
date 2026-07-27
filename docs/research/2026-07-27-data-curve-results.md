# The data curve — 21× the data buys 3.3 points, and my mechanism was wrong

**Date:** 2026-07-27 · **Pre-registration:** `2026-07-26-data-curve-preregistration.md`
**Prediction under test:** `2026-07-26-frame-capture-prediction.md`

Closes the last fidelity gap the write-up admits: **460 training examples against the paper's
10,000–100,000**. The generator's ceiling was lifted from 1,540 to ~5.3M so the range was reachable
at all, and five arms were trained at identical hyperparameters, differing only in dataset size.

## Results — wide set (n=61)

| N | greedy | card (temp 1.0) |
|---|---|---|
| 460 | 0.853 | 0.831 ± .016 |
| 1,000 | 0.853 | 0.858 ± .016 |
| **2,500** | **0.885** | 0.853 ± .033 |
| 5,000 | 0.836 | 0.803 ± .000 |
| 10,000 | 0.836 | 0.814 ± .049 |

**Void check: PASSED.** The N=460 arm retrained from the widened vocabulary to **0.853** against its
published **0.820** — drift 0.033, inside the pre-registered 0.05 tolerance. The generator did not
move underneath the comparison, so the curve measures data scale rather than itself.

**Verdict: PLATEAU**, by the rule fixed before any data existed. The best arm above baseline (2,500 at
0.885) gains **0.033** over N=460 — below the 0.05 threshold, which is the same tolerance round 1's
promotion gate used.

**Twenty-one times the data buys 3.3 points, and past 2,500 it goes backwards.** N=5,000 and
N=10,000 both land at 0.836, *below* the 460-example baseline.

## What this does and does not license

At **fixed compute** — every arm 400 iters × batch 4 = 1,600 examples seen — larger datasets mean
more *unique* rows and fewer repeats. N=460 sees each row ~3.5 times; N=10,000 sees 1,600 of its
10,000 rows once and never sees the rest.

So the honest reading is: **seeing 1,600 unique examples once is no better than seeing 460 examples
three and a half times.** Repetition is doing as much work as diversity at this scale. That is a
result about a training budget, not a refutation of the paper's data range, and the pre-registered
disambiguation (one epoch-matched arm at 2,500) is running to separate the two.

Validation loss was **useless** for predicting any of this: every arm converged to ~0.001, including
N=10,000 at 0.16 epochs. Finding 4 said validation loss is not target accuracy; here it does not even
rank the arms.

## My frame-capture prediction was refuted — as pre-registered

I predicted a mechanism and recorded it before the curve ran: `read_file` lost one row
(*"Throw internal/auth/jwt.go on screen."* → `write_file(path="screen", …)`) because the tuned model
had learned the write templates' **verb + thing + preposition + place** frame and the frame overfired.
The prediction: more rows of the same shapes would not fix that row and might entrench it, while
`search` and `write_file` kept improving.

Per tool across the curve (FRESH n=48, greedy), with the frame row tracked:

| N | read_file | search | run_tests | write_file | frame row |
|---|---|---|---|---|---|
| 460 | 10/12 | 9/12 | 12/12 | 9/12 | ✗ |
| 1,000 | 10/12 | 11/12 | 11/12 | 9/12 | ✗ |
| 2,500 | 11/12 | 11/12 | 11/12 | 8/12 | **✓** |
| 5,000 | 10/12 | 9/12 | 11/12 | 9/12 | ✗ |
| 10,000 | 11/12 | 8/12 | 12/12 | 7/12 | **✓** |

**Every part of the prediction failed.**

1. The frame row is **fixed at N=2,500 and N=10,000** and wrong at 460/1,000/5,000. It does not
   entrench — it *flips*.
2. `search` and `write_file` **decline** with scale (9→8 and 9→7), the opposite of predicted.
3. The predicted divergence appears with the signs reversed: `read_file` slightly improves while the
   others degrade.

The pre-registration said: *"If the row is fixed at N=10,000 the mechanism is wrong and that gets
written down rather than replaced with a second explanation."* It is fixed. **The mechanism is
wrong.**

### What I actually did

I built a detailed, mechanistically plausible, well-argued explanation **on a single row**. It
survived exactly as long as it took to get a second measurement. The row flips with dataset size,
which is what a marginal, noise-dominated example looks like — not what a learned syntactic frame
looks like.

**Finding 21: a mechanism inferred from one row is a story, not a finding.** The failure mode is
specific and seductive: the row was *legible*. I could read the training templates, see the shared
frame, and construct a causal account that explained the observation perfectly. Explaining one data
point perfectly is not evidence — it is overfitting, performed by me rather than by the model.

### And the reason it was unfalsifiable in the first place

The per-tool cells are **12 rows each**. Every movement in that table — including the ones I
predicted, and the ones that actually happened — is within ±2 of 12 across a 21× data range. **The
wide set is adequate for aggregate claims at n=61 and inadequate for per-tool claims at n=12**, and
I made a per-tool claim from it.

That is finding 18 in a mirror. There, identical scores on a saturated instrument read as
equivalence; here, moving scores on an underpowered slice read as mechanism. **Both are the
instrument talking.**

## Robustness held across the whole curve

Card-point spreads stay tight at every size (0.016–0.049), so finding 20 — fine-tuning buys
insensitivity to sampling configuration — is not a property of one dataset size. It appears at 460
examples and survives to 10,000.

## Next

1. The **epoch-matched arm at 2,500** (2,175 iters ≈ 3.5 epochs, matching what N=460 received) is
   training. If it beats fixed-compute 2,500, the plateau was a compute artifact; if not, data
   sufficiency at this template diversity is real.
2. Per-tool conclusions need ~40 rows per tool, not 12. Until then, no claim about *which* tool
   scaling helps should be made — including the ones above, which is why they are reported as noise
   rather than as a shape.
