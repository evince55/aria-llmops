# S10 ensemble — PRE-REGISTRATION (written before any ensemble was run)

Committed **before** running anything, because I have already seen the row-level failures of both
arms (`docs/research/2026-07-26-s10-native-tools.md`). A combination rule invented after seeing
which rows each arm misses is fitted to 13 examples and would measure my memory, not the method.
Finding 14 is about exactly this class of self-deception.

## The prediction being tested

The two arms fail on **disjoint** adversarial tasks — both 11/13, zero overlap, four disagreements
two each way. If failures are genuinely uncorrelated, then:

- **P1.** Where the arms *agree*, they are right. Predicted agreement precision **1.00**.
- **P2.** Agreement covers about 9/13 of rows; the other 4 are the disagreements.
- **P3.** A majority vote needs a third arm, and can only beat 11/13 if that third arm is right
  more often than chance on precisely the 4 contested rows.

## Rules, fixed now

**Rule A — agree-or-escalate cascade.** Both arms answer. Identical call → accept. Different call
(or either fails to produce one) → **abstain and escalate**. Reported as coverage, precision on
covered rows, and escalation rate. This is *selective prediction*, not an accuracy claim: it cannot
raise accuracy, it can only tell you when to trust the small models.

**Rule B — 3-arm majority vote.** Accept the call emitted by at least 2 of 3 arms; no majority →
abstain. Reported as accuracy over all rows.

## Arm C is chosen now, and will not be swapped

**Arm C = `Qwen3.5-9B-MLX-4bit`, prose interface.** Chosen for *independence*: E2B-tuned and
E2B-base share a base model, and Ornith-native and Ornith-prose are the same weights, so neither is
a real third opinion. Qwen is a different family entirely.

**If arm C scores poorly, that is the result.** It will not be replaced with a better-performing
model after the fact. Swapping an arm after seeing its score is the straw-man error of finding 14
run in reverse.

## What would falsify the prediction

- Agreement precision below 1.00 → the arms *do* share failure modes and the disjointness was luck.
- Majority vote at or below 11/13 → a third arm does not resolve the contested rows, and ensembling
  buys nothing on this task.

## Stated in advance: this set is too small to settle anything

n=13, of which **4 rows carry the entire signal**. Every number below is an existence proof at best.
Any conclusion about *which* rule is better needs the wider adversarial set, which does not exist
yet. Reporting the result is worthwhile; treating it as a decision is not.
