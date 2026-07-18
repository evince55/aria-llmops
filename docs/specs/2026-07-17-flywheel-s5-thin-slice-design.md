# Flywheel S5 — thin end-to-end slice (design)

**Date:** 2026-07-17 · **Status:** approved design, implementing
**Roadmap:** closes the untouched core of the flywheel — steps **S5 (fine-tune)** and
**S6 (eval-gated promotion)** of the SLM-agents conversion algorithm (arXiv 2506.02153 §6),
which R1 (measurement) and R2 (intake) were all built to feed.

## Goal of this increment

Prove the three unbuilt flywheel stages work **end to end on a small batch**, and produce
one real (deliberately underpowered) accuracy number for a fine-tuned small model vs the
incumbent 9B classifier — **before** spending on data scale. This is a plumbing +
feasibility proof, **not** a promotion.

Explicit non-goals: promoting any model; hitting the paper's 10k–100k volume; claiming the
fine-tuned model is production-ready. Those are the *next* increment, cost-gated on this
one's results.

## What the fine-tuned model is for

Replace the **9B** in the production hybrid router (`classify_hybrid`: keyword-first +
9B-rescue). The 9B only fires on **keyword-blind prose** tasks (the ~66% no-claim pool from
the SOL work). So the training distribution is prose tasks where the keyword classifier
defaults — the exact regime the rescue serves.

## Teacher (decided)

A **stronger model labels the tiers**, so the student can *exceed* the 9B rather than clone
its known severity blind spots. Teacher = **`opencode-go/minimax-m3`** (proven capable in
the Arm-A experiments; scriptable; ~$1–3 for the thin-slice volume). It both (a) generates
paraphrase variations of each real seed task and (b) assigns the tier using the router's
exact 4-tier rubric at temperature 0.

## Base-model bake-off (decided)

Same distilled data, three roles by what each architecture permits:

| Model | Size | Role | Why |
|---|---|---|---|
| **Gemma-4-E2B** | ~3.2 GB | **fine-tune target** | smallest that fits the Air comfortably; the ideal 9B replacement |
| **Gemma-4-E4B** | ~5.0 GB (on NVMe) | **fine-tune target** | accuracy-per-GB comparison vs E2B |
| **Bonsai-27B-mlx-1bit** | ~3.9 GB | **zero-shot baseline** | CANNOT be LoRA'd (custom 1-bit kernels, no fp16 escape hatch, no q/k/v projections — confirmed via model card). Tests "big-but-1-bit zero-shot vs small-but-fine-tuned" at the same ~4 GB footprint |
| **Qwen3.5-9B** | ~5.8 GB | **incumbent baseline** | the number everything is measured against |

Cheap hedge: **attempt** `mlx_lm.lora` on Bonsai first; if it works (unexpected) it graduates
to a fourth fine-tune target; when it fails (expected) it stays the zero-shot baseline.

## Pipeline (4 testable stages)

1. **Seed harvest** — `telemetry.py flywheel export --enrich-tiers` → the real distinct
   prose tasks. Distribution anchor.
2. **Teacher expansion + labeling** — `evals/distill_generate.py` (new): for each seed, drive
   minimax-m3 to emit K variations, each labeled with a tier (router rubric, temp 0). Output
   `evals/datasets/distilled/train.jsonl` (gitignored — contains task text) with
   `{task, tier, source: seed|synthetic, teacher, ts}`.
3. **Fine-tune** — `mlx_lm.lora` (LoRA/DoRA) on E2B and E4B on the Air. Adapters saved under
   `evals/adapters/<model>/`. Serial (one GPU).
4. **Eval-gate (S6, report-only)** — wrap each tuned model as a `classify(task)->tier`
   callable and run the existing `evals/router_classification_eval.evaluate(classify=...)`
   on the **quarantined 42-example human set + prose set**. Report accuracy for: E2B-tuned,
   E4B-tuned, Bonsai zero-shot, 9B incumbent. No promotion decision.

## Non-negotiables (carried from prior work)

- **Quarantine**: the labeled eval datasets (`labeled_tasks*.jsonl`) are NEVER a training
  input. `flywheel export` already enforces this; `distill_generate` must re-assert it
  (seeds come from the harvested ledger, not the eval sets; assert zero overlap).
- **Provenance**: every example tagged `source: seed|synthetic` so real-vs-teacher is always
  separable in later analysis.
- **Privacy**: `train.jsonl` and adapters are gitignored (contain prompt text / derived
  weights), like the ledger.
- **Stdlib runtime** for the router itself is unchanged; mlx-lm is a **dev/training-only**
  dependency (added to `requirements-dev.txt`, never imported by `llmops.py`).

## Hardware reality (shapes execution, not design)

One Mac GPU + one Windows box. Training E2B and E4B **cannot** run concurrently → serial.
Teacher generation is a **cloud API** (minimax-m3) → genuinely parallelizable across seed
batches. So: data-gen fans out; training/eval is serial and driven from the main loop.

## Recon gates (must pass before build/run; #1 is the long pole)

1. **mlx-lm LoRA actually works on this Mac** — smoke-test a tiny LoRA run on a small
   Gemma-4 before trusting the pipeline (import signal was ambiguous).
2. **E2B acquisition + E4B trainability** — E2B not on the NVMe (download an MLX build); the
   NVMe E4B is 4-bit — confirm `mlx_lm.lora` trains a 4-bit base or fetch a bf16/higher build.
3. **Teacher prompt validated** — a 5-example micro-batch through minimax-m3 returns
   well-formed `{variation, tier}` before generating the full thin-slice batch.
4. **Bonsai** — confirm zero-shot load + classify works; confirm LoRA attempt fails cleanly.

## Success criteria for the increment

- All four pipeline stages run without manual patching after recon.
- A results table (4 models × accuracy on the quarantined eval) exists and is written to
  `docs/research/`.
- Honest framing throughout: underpowered n, weak-ish teacher labels, no promotion.
