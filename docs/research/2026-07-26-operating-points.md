# Every arm at both operating points — the conclusion holds, and fine-tuning bought robustness

**Date:** 2026-07-26 · **Closes** the gap finding 19 opened.

Finding 19 was written about Ornith, which I had forced to `temperature: 0` while its authors
benchmark it at 1.0. Applying the same standard to the local arms turned up the **mirror-image**
defect, and re-running everything at both points produced a result I did not anticipate.

## The defect, locally

```
gemma-4-e2b-it-4bit / generation_config.json
  temperature 1.0, top_p 0.95, top_k 64, do_sample true
```

**Every E2B number this repo has published was taken greedy.** `mlx_lm.generate` was called with no
sampler argument at all — greedy *by omission*. Ornith was measured off-spec in one direction and the
local arms in the other, and in neither case had anyone decided anything: the defaults simply went
unnamed.

## The 2×2 — wide set, n=61

| Arm | greedy | card (1.0 / 0.95 / 64) | spread at card |
|---|---|---|---|
| E2B base | 0.541 | 0.530 | 0.115 |
| **E2B + tool adapter** | **0.820** | **0.820** | **0.066** |
| Ornith native | 0.623 | 0.563 | 0.049 |
| Ornith prose | 0.574 | 0.508 | 0.164 |

**The headline is unchanged and now unconditional.** The tuned 3.2 GB model beats the 9.5 GB
tool-tuned model at *every* combination of interface and operating point — 0.820 against 0.623 at
Ornith's best, 0.820 against 0.563 at its card. Conversion beats selection regardless of how either
model is sampled.

## Finding 20 — fine-tuning a narrow subtask buys sampling robustness, not just accuracy

The tuned arm scores **0.820 greedy and 0.820 mean at the card point**. Identical. On the FRESH
slice, **0.812 and 0.812**. Temperature 1.0 with `top_k 64` — a genuinely loose sampler — barely
moves it, while the same sampler costs the base model accuracy *and* doubles its run-to-run spread:

| Arm | spread over 3 runs at temp 1.0 |
|---|---|
| E2B base | 0.115 |
| **E2B + tool adapter** | **0.066** |

QLoRA on 460 examples sharpened the output distribution enough that the model became **insensitive to
its own sampling configuration**. That is operationally more valuable than it first sounds: a
converted model does not need its serving config to be right. The base does — and this project has
now twice been burned by exactly that (finding 12's `--repeat-penalty 0`, finding 19's temperature).

I did not predict this and would not have found it by measuring one operating point, which is the
argument for the whole exercise.

## Finding 16, repaired

The original claim — *constrained decoding removes run-to-run variance* — was tested at temperature
0, where **there is no sampling to constrain**. Comparing two deterministic paths measures nothing,
which is why it looked unreproducible when re-probed.

Measured where sampling actually happens, it is **true**:

| Ornith at temp 1.0 | spread |
|---|---|
| native `tools` | **0.049** |
| prose | 0.164 |

A tool grammar cuts run-to-run variance by ~3.3×. So there are two ways to sharpen a model's output
distribution — **fine-tune it, or constrain the decoder** — and this round measured both.

## What is now enforced in code

- `operating_point(name, …)` builds a **named** configuration and **raises** on an unknown name.
  There is deliberately no fallback: a silent default is precisely how this went wrong twice.
- `card_point(base)` reads the sampling config from the checkpoint's **own files** and raises if it
  declares none. An invented operating point is worse than an absent one. (Qwen3.5-9B ships no
  `generation_config.json`; that is reported, not guessed.)
- `is_deterministic(point)` gates repeats — above temperature 0 a single run is a *sample*, so
  `--runs` is honoured there and collapsed to 1 below it. Every result carries
  `{n_runs, scores, mean, spread}`.
- The operating point travels in every result's arm metadata, so a run cannot be silently off-spec.

**"At spec" is not automatically the card's value, and the code does not pretend otherwise.** A card
temperature is chosen for open-ended generation; this subtask has one correct answer per task, where
greedy is the defensible engineering choice. The two settings answer different questions — *how does
it behave as shipped* versus *how well can it do this job* — and naming the point is what makes an
answer say which one it is reporting.

## Next

- Qwen (arm C) declares no sampling config, so it still has no defensible operating point and remains
  out of the wide comparison. Establishing one from its model card is the remaining gap.
- The greedy re-runs reproduced 0.541 and 0.820 exactly, so nothing above supersedes the wide-set
  results — it extends them along an axis that was previously unmeasured and unnamed.
