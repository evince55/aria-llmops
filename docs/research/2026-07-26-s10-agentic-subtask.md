# S10 — the reproduction's missing claim, on an agentic subtask

**Date:** 2026-07-26 · **Closes:** the sharpest fidelity gap in `docs/REPRODUCTION.md`
**Result: a fine-tuned 3.2 GB model beats both its own base and a selected 9.5 GB tool-tuned
model on tool-call emission.** Cost to run: **~$0** — no LLM judge, no teacher, no subscription.

The reproduction converted a single-turn *classifier* while the paper is about replacing LLM
agents in *agentic* subtasks. This converts an agentic subtask instead.

## The target was chosen by measurement

The write-up's first "what I'd do differently" was **choose the converted component by leverage**.
So two candidates were probed on the conversion target before committing to either:

| candidate | strict accuracy | verdict |
|---|---|---|
| code edit (file in → edited file out) | **1.00** | ceiling — nothing to learn |
| tool call (task + schema → JSON call) | **0.70** | 30 points of headroom |

The code-edit harness already existed and was the convenient choice. It was dead on arrival.

## Everything is verified programmatically

Grading is exact structural comparison against a call authored *with* the task. No judge, so:
no cloud cost, no verbosity bias, and no possibility of the failure that voided A2b — a judge
grading a task its harness could not perform. Ground truth is correct by construction, so no
teacher distillation is needed either; that is the whole reason this round is free.

## Results

Two held-out sets. The **standard** set (n=20) is the original eval; the **adversarial** set
(n=13) uses phrasings, file extensions and multi-word contents absent from *both* the training
templates and the standard set.

| arm | size | standard | adversarial |
|---|---|---|---|
| E2B base | 3.2 GB | 0.55 | 0.69 |
| **E2B + tool adapter** | **3.2 GB** | **1.00** | **0.85** |
| Ornith-1.0-9B (tool-tuned) | 9.5 GB | 0.85 | 0.62 |

**Fine-tuning is worth +45 points on the standard set and +16 on the adversarial one.** The
adversarial figure is the honest estimate of generalisation: 1.00 on n=20 cannot be
distinguished from 0.95, and the harder set is where the difference shows.

What the adapter actually learned is visible in the decomposition: **parse rate 0.92 → 1.00**
(it always emits valid JSON now) with tool accuracy unchanged at 0.92. It did not learn *which*
tool to call — the base already knew — it learned to **emit the call correctly and fill the
arguments exactly**. That is precisely the narrow, repetitive competence the paper claims small
models can absorb.

It also generalised the one thing it was specifically asked to: training says *"terse output" /
"silently" / "keep it brief"*, the held-out set says *"quietly" / "no extra output"*. The boolean
had to be inferred, not matched.

## On Ornith, and being fair to it

Ornith-1.0-9B is fine-tuned for coding and tool use, so it is a strong **S4 (select)** arm — the
pipeline stage this reproduction had covered worst. It beat the E2B base on the standard set
(0.85 vs 0.55) and lost to it on the adversarial one (0.62 vs 0.69).

**That number should not be read as "Ornith is worse."** Its failures decompose as 2 unparseable,
2 wrong tool, 1 wrong args — a tool accuracy of 0.69 for a *tool-tuned* model is a red flag for
**format**, not capability. Ornith is trained against a native tool-calling interface; this prompt
asks for freeform JSON in prose, and it is also measured through llama.cpp while the E2B arms run
through MLX directly. A fair test would use its native tools API on the same stack. Until then the
Ornith column is indicative, not a verdict.

## Two harness bugs caught in my own work

**1. I measured the base twice and labelled one "tuned."** `mlx_lm server` resolves models *by
path on demand*, so `--adapter-path` was silently ignored and the "tuned" arm returned exactly the
base's numbers. Direct probing on three previously-failing cases exposed it: base 0/3, tuned 3/3.
`tool_call_eval.py` now loads adapters via `mlx_lm.load(..., adapter_path=)`.

**2. I leaked the eval phrasings into training.** Exact-task quarantine passed — no task appeared
in both — but the training templates had been written by mirroring the held-out wording, and the
tuned arm scored a perfect 1.00 that measured memorisation. Fixed structurally: train-only
phrasings, and `phrasing_overlap()` refuses to generate a leaking set.

**Quarantine on tasks is not quarantine on phrasings.** That is a new finding, and it is the
thirteenth for the write-up.

## Fidelity, updated

| Dimension | Paper | Before S10 | After S10 |
|---|---|---|---|
| Task type | agentic subtasks | single-turn classification | **tool-call emission** ✓ |
| Training examples | 10k–100k | 677 | 460 (still ~5%) |
| Verification | — | LLM judge | **deterministic** ✓ |

The scale gap remains and is not addressed here.

## Next

1. **Test Ornith through its native tool-calling interface** before drawing any conclusion about
   selection-vs-conversion. The current comparison is confounded by prompt format and stack.
2. Widen the adversarial set — n=13 is thin for a 0.85.
3. The scale gap (5% of the paper's data) is still the largest remaining divergence.
