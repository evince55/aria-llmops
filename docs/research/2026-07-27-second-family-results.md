# The second family — it is the pipeline, and a chat template nearly said otherwise

**Date:** 2026-07-27 · **Pre-registration:** `2026-07-27-second-family-preregistration.md`

Closes the last open row in the write-up's fidelity table. The paper uses several model families;
this reproduction converted one, and a reproduction that converts a single family cannot tell a
method from a lucky checkpoint.

## Results — wide set (n=61), greedy, Schedule B, all four arms sound

| Arm | Size | strict | tool | out tokens |
|---|---|---|---|---|
| Gemma-4-E2B base | 3.2 GB | 0.541 | 0.918 | — |
| **E2B + tool adapter** | 3.2 GB | **0.885** | 0.918 | 24 |
| Qwen3.5-9B base | 5.6 GB | 0.754 | 0.951 | 35 |
| **Qwen3.5-9B + tool adapter** | 5.6 GB | **0.885** | 0.984 | 23 |

**Verdict: PIPELINE.** The Qwen conversion gains **+0.131**, above the pre-registered 0.10 floor.
Round 2's result is not a Gemma artifact.

**Both converted arms land on exactly 0.885 — 54 of 61, identically.** Two different families, two
different sizes, same subtask, same 460 examples, same schedule, same score. The bases differ by 21
points (0.541 vs 0.754) and conversion erases the difference entirely. Whatever the adapter is
teaching, both models end up knowing the same amount of it.

Qwen's larger base does show somewhere: its converted arm has the best tool selection of any arm
measured (0.984 vs 0.918).

## The chat template nearly produced the opposite verdict

The first attempt served Qwen through its template's **default reasoning mode**. The tool-call
training data contains no reasoning — prompt in, one JSON line out — so this asked the adapter for
something it was never trained to produce.

| Qwen, thinking ON (first attempt) | strict | out tokens | truncation | sound |
|---|---|---|---|---|
| base | 0.738 | 1,222 | 0.230 | **False** |
| tuned | 0.836 | 1,272 | 0.311 | **False** |

Gain **+0.098** — which is *below* the pre-registered 0.10 floor. **The harness would have reported
GEMMA-SPECIFIC, and the write-up would have said round 2's headline was substantially a Gemma
result.** That conclusion was wrong, and the only thing standing between it and publication was
`sound: False`.

Worse than the truncation itself: **11 of 19 truncated rows in the tuned arm were graded correct.**
That is finding 17's mechanism operating at scale — the parser harvesting a draft call out of a
generation that never finished. The inflated number was not merely noisy, it was manufactured by my
own harness, and it was inflated *in the direction that made the tuned arm look better while still
losing the comparison*.

The fix is one kwarg, and the effect is not subtle — same model, same adapter, same task:

```
default (thinking on) :  661 tokens  →  …</think>\n\n{"tool": "read_file", …}
enable_thinking=False :   21 tokens  →  {"tool": "read_file", …}
```

## The pre-registered sub-question, answered — and not the way I expected

*Does QLoRA on 460 examples teach a reasoning model to stop reasoning?*

**No. The template did.** With thinking on, the **tuned** model still emitted 1,272 tokens — *more*
than the base's 1,222. Four hundred iterations of fine-tuning on 460 single-line examples, with
training loss at 0.000, did not suppress a reasoning block the chat template opens.

The adapter had learned the output format perfectly: given a template that lets it answer, it emits
21 tokens of clean JSON. It simply could not override the template's mode. **Fine-tuning changes what
a model says, not the scaffold it is made to say it inside.**

## Finding 23 — a chat template mode is part of the operating point

Finding 19 established that temperature is not a free parameter and must be named and recorded. The
template mode is the same class of thing and was not covered: nothing in the harness recorded which
mode a run used, so two arms could be served differently while the metadata claimed they matched.

`apply_template(tok, prompt, thinking)` now names it, records it in arm metadata, and — deliberately
— reports `None` when the kwarg was never passed, rather than reporting a mode as though it had been
chosen. Inheriting the default *is* what went wrong, so it is recorded as inheritance, not as a
decision. A template with no such kwarg (Gemma has none) also records `None`, because silently
succeeding would let the two arms diverge invisibly.

## What the crash cost, and what it taught

The first attempt at this round **crashed the machine**: the E2B schedule applied unchanged to a 9B
demanded **25.7 GB peak on a 16 GB Mac**, and the run had already diverged to a `2³²/100` overflow
sentinel. "Protocol inherited, not re-invented" is a claim about the *experiment*; batch size and
gradient checkpointing are *resource* parameters whose cost scales with model size, and holding them
fixed protected nothing.

Schedule B (batch 1, seq 512, lr 1e-5, grad-checkpoint, 1600 iters) runs at 7.1 GB for Qwen and
3.4 GB for E2B. Both arms were retrained under it so the family comparison stays controlled —
`seq 512` and `grad-checkpoint` were verified to be non-confounds (longest training row: 137 tokens),
while batch size and learning rate are genuine ones.

## Status of the fidelity table

Every row is now closed or marked stronger than the paper. Model family was the last one.
