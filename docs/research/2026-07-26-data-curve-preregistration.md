# Data-efficiency curve — PRE-REGISTRATION (written before any dataset was generated)

The scale gap is the last fidelity divergence `docs/REPRODUCTION.md` admits and never tests: **460
training examples against the paper's 10,000–100,000**. Everything else has now been closed or
measured. This tests it.

Committed before generating a single row, because the outcome I *want* (an early plateau — a result
that contradicts the paper's stated data requirement and would be this reproduction's only novel
contribution) is exactly the outcome I could manufacture by choosing a schedule that starves the
large runs.

## The blocker, measured first

The current generator **saturates at 1,540 unique examples** — ask it for 20,000 and it returns
1,540. The paper's range is not merely untested here, it is currently unreachable. So step one is
vocabulary expansion, and step one is also where a bias could enter: expanded vocabulary must stay
disjoint from all three eval sets, enforced by the guards that already exist.

## Sizes

**460 (the existing adapter's size), 1,000, 2,500, 5,000, 10,000.**

## Training schedule — fixed compute, and why

Every arm trains at **iters 400, batch 4, rank 8, lr 1e-4, max_seq 768** — identical to the existing
adapter, changing only the dataset.

That is **fixed compute, not fixed epochs**. At N=460 the model sees each example ~3.5 times; at
N=10,000 it sees 1,600 of 10,000 rows once and never sees the rest. The curve therefore answers:

> **At a fixed training budget, does more *unique* data help?**

which is the question a practitioner with one laptop actually faces. It does **not** answer "does a
model trained to convergence on 10k beat one trained to convergence on 460" — matched epochs at
10,000 would be ~8.75 hours of local compute per arm, and four such arms is not a reasonable spend
for a question this can answer more cheaply first.

## The confound I must not talk myself out of

**A plateau has two explanations and the primary curve cannot separate them:**

1. SLM conversion on a narrow subtask genuinely needs little data — the interesting result.
2. Fixed compute starved the large arms, so they plateaued for lack of *steps*, not lack of value
   in the data — a boring artifact that would look exactly the same.

There is a third, subtler one specific to how this data is made: expanding **vocabulary** while
holding **templates** fixed multiplies the number of rows without adding linguistic diversity. A
plateau might mean "more rows of the same shapes teach nothing new," which is a statement about my
generator, not about SLM conversion.

**Pre-registered disambiguation.** If the curve plateaus, I run **one** epoch-matched arm at the
largest tractable size (2,500 at ~3.5 epochs ≈ 2,190 iters, ≈2 h) before drawing any conclusion. If
that arm beats its fixed-compute twin, the plateau was compute. If it does not, the plateau is real
at this level of template diversity — and that caveat ships with the claim.

## Decision rule, fixed now

- **Plateau** = the best arm above N=460 improves strict accuracy on wide (n=61) by **< 0.05** over
  the N=460 arm. 0.05 is the same tolerance the round-1 promotion gate used; it is not chosen here.
- **Scaling** = monotone-ish improvement exceeding 0.05 across the range.
- **Regression** = any arm materially below N=460 — which would indicate the expanded vocabulary
  changed the data distribution for the worse, not that data hurts.

Every arm is evaluated on **wide (n=61) at both operating points** (greedy, and the card point
`temp 1.0 / top_p 0.95 / top_k 64`, 3 runs), because finding 20 showed the tuned model's robustness
to sampling is itself an outcome — and a data curve might move robustness even where it does not
move accuracy. Spread is reported, never a single card-point run.

## What would falsify the exercise

If the N=460 arm does not reproduce its published 0.820 on wide when retrained from regenerated
data, the generator changed underneath the comparison and every number in the curve is measuring
that instead. **The N=460 arm is retrained from the new vocabulary and must land near 0.820**, or
the curve is void and gets reported as void.
