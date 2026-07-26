# The wide set — the tie was a ceiling artifact, and conversion wins by 0.20

**Date:** 2026-07-26 · **Pre-registration:** `2026-07-26-wide-set-preregistration.md`
**Supersedes** the round-2 conclusions in [#51](https://github.com/evince55/aria-llmops/pull/51)
and [#52](https://github.com/evince55/aria-llmops/pull/52).

n=13 → n=61, plus every served measurement redone at the model's own specified temperature. Both
changes were needed and both move the same claims.

## Results

| Arm | Config | wide (n=61) | FRESH (n=48) |
|---|---|---|---|
| Gemma-4-E2B base | greedy | 0.541 | 0.479 |
| **E2B + tool adapter** | greedy | **0.820** | **0.812** |
| Ornith-1.0-9B native | temp 0 *(its best)* | 0.623 | 0.562 |
| Ornith-1.0-9B native | temp 1.0 *(benchmarked)* | 0.563 ± .049 | 0.514 |
| Ornith-1.0-9B prose | temp 1.0 | 0.508 ± .164 | 0.486 |

**Conversion beats selection by ~0.20**, and the margin is robust across Ornith's entire temperature
range: even at its best configuration (native interface, greedy) it reaches 0.623 against the tuned
3.2 GB model's 0.820. At 34% of the memory.

## Finding 18 — a tie on a saturated instrument is not a tie

[#51](https://github.com/evince55/aria-llmops/pull/51) concluded "conversion **ties** selection"
because both arms scored **1.00 on n=20** and **0.846 on n=13**. Identical numbers, twice. I read
that as equivalence.

It was a **ceiling**. Both arms were saturating an instrument too easy and too small to separate
them; on n=61 they differ by 0.197. The tie carried no information and I treated it as a finding.

This is the third variant of the same mistake in this project, and the pattern is now explicit:
finding 14 was an instrument shaped for the wrong model, finding 15 an instrument shaped for the
wrong output style, and this one an instrument with no headroom left to measure in. **Identical
scores are evidence about the instrument at least as often as they are evidence about the models.**

The corollary matters for the write-up's method section: *every* number this project reported from
n=13 or n=20 should be read as a lower bound on uncertainty, not as a measurement.

## What the fine-tuning gain actually is

Three published values for the same quantity, each on a better instrument than the last:

| Instrument | Fine-tuning gain (adversarial) |
|---|---|
| n=13, greedy regex parser | +15.6 |
| n=13, parser fixed | +7.7 *(one task)* |
| **FRESH n=48** | **+33.3** |

The n=13 base score (0.769) was noise — the same arm scores **0.479** on FRESH, a 29-point
collapse — so the pre-registration's falsification condition fired and that number is **withdrawn,
not averaged**. The tuned arm barely moves (0.846 → 0.812), which is what a real capability looks
like when the instrument gets harder.

## The gain is where the training aimed, not everywhere

FRESH slice, per tool:

| Tool | base | tuned |
|---|---|---|
| `read_file` | 11/12 | 10/12 |
| **`run_tests`** | 5/12 | **12/12** |
| `search` | 3/12 | 9/12 |
| `write_file` | 4/12 | 8/12 |
| *distractor rows* | 3/5 | **5/5** |

`run_tests` going 5/12 → 12/12 is the paper's thesis behaving exactly as advertised. Training taught
verbosity inference from *"terse" / "silently" / "keep it brief"*; the fresh set asks with *"full
firehose" / "chatty mode" / "hush" / "keep it to a summary"* — idioms the model has never seen — and
it gets all twelve. `read_file`, where the base was already competent, shows **no gain and a small
cost**. A uniform lift would have suggested the eval was measuring something generic; this is
localised to what was trained.

The distractor axis (a task naming another tool's noun — *"Before running anything, just read
ops/deploy-notes.txt"*) separates the arms cleanly: base 3/5, tuned 5/5. Ornith is hit hardest of
all — its **tool accuracy on FRESH is 0.688**, so it picks the wrong tool on nearly a third of them.

## The ensemble result survives the harder set

Rule A (agree-or-escalate), n=61:

| | n=13 | **n=61** |
|---|---|---|
| coverage | 0.692 | 0.525 |
| **precision on covered** | 1.00 | **1.00 (32/32)** |
| escalation | 0.308 | 0.475 |

**Agreement remains a perfect trust signal on 3.5× the rows** — this is now the best-evidenced claim
in round 2, and unlike the accuracy numbers it did not move. Coverage falls because the arms diverge
far more on genuinely hard tasks, which is the correct behaviour: the cascade escalates more when
the work is harder.

## Temperature, and why it is not the explanation here

Every earlier measurement forced `temperature: 0`, overriding the server's own `--temp 0.6` on a
model benchmarked at 1.0. That was an off-spec measurement and it did move two findings (see the
commit for the standard/adversarial re-runs). But on the wide set, temperature accounts for only
**6 points** of Ornith's shortfall (0.623 greedy vs 0.563 at 1.0) while the gap to the tuned model
is **20**. Set difficulty dominates; temperature does not rescue the selected arm.

Two diagnostics, run before concluding: the request-level temperature **is** honoured (temp 0 → 1
distinct reply in 4, temp 0.6 → 3, temp 1.5 → 2 with an outlier), and the temp-0 answer does not
depend on preceding requests. Ornith at temp 0 on n=61 also returned **0.623 twice, exactly** — so
the temp-0 nondeterminism behind finding 16 could not be reproduced today, and that finding is
weakened accordingly rather than restated.

## What is deliberately not here

**Qwen (arm C) was not run on the wide set,** so there is no Rule B number. Running it at an
unexamined operating point would repeat the error this round just corrected — the MLX path is still
greedy for every arm, which is at spec for nothing in particular. Per-arm operating points are
tracked separately, and the E2B numbers above carry the same caveat as Ornith's did: they are one
configuration, not a characterisation.

## Next

1. Establish each arm's specified sampling config and re-run everything at it, with repeats where
   temperature > 0. The E2B arms are greedy by default, not by argument.
2. `read_file` is the one place fine-tuning *cost* accuracy (11/12 → 10/12). Small, but it is the
   only regression and it is worth understanding rather than rounding away.
3. n=61 is a real instrument; n=13 was not. Nothing measured only on the old set should be quoted
   without re-measurement here.
