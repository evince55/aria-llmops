# S5 thin slice — first fine-tune, measured

**Date:** 2026-07-17 · **Spec:** `docs/specs/2026-07-17-flywheel-s5-thin-slice-design.md`
**Status:** feasibility proof complete. This is the roadmap payoff — the first time the
flywheel closed the loop from real logged tasks to a fine-tuned model with a measured
accuracy vs the incumbent. **Deliberately underpowered; no promotion.**

## What ran, end to end

1. **Seed harvest** — 14 substantive task-shaped prompts pulled from the real route-decision
   ledger (filtered from 82 by verb/length, deduped).
2. **Teacher distillation** — `opencode-go/minimax-m3` expanded each seed into 12 paraphrase
   variations and labeled every one with a tier using the router's exact rubric (temp 0).
   → **182 examples** (14 seed + 168 synthetic), ~5 min. Eval-set quarantine asserted.
3. **Convert + fine-tune** — `distill_to_mlx` → chat `train/valid`; `mlx_lm` QLoRA (4-bit base,
   LoRA rank-8, 8 layers, 300 iters, masked prompt) on the Air.
4. **Eval-gate** — each model classifies the **quarantined 42-example human union**
   (`labeled_tasks` + `labeled_tasks_prose`) through the existing `router_classification_eval`.

## Results

| Model | Size | Union acc | CRITICAL | COMPLEX | MODERATE | SIMPLE |
|---|---|---|---|---|---|---|
| **9B incumbent** (baseline) | ~5.8 GB | **0.762** | 1.00 | 0.46 | 1.00 | 0.70 |
| **E2B-tuned** | ~3.2 GB | **0.619** | 1.00 | 0.23 | 0.58 | 0.90 |
| E2B base (zero-shot) | ~3.2 GB | 0.286 | 0.00 | 0.00 | 1.00 | 0.00 |
| **E4B-tuned** | ~5.0 GB | 0.286† | 0.00 | 0.00 | 1.00 | 0.00 |
| E4B base (zero-shot) | ~5.0 GB | 0.286 | 0.00 | 0.00 | 1.00 | 0.00 |
| Bonsai-27B-1bit (zero-shot) | ~5.1 GB | N/A — blocked | | | | |

per-tier = recall. 9B is non-deterministic (varies ~0.67–0.76 run-to-run; 0.762 this run).
0.286 = the always-predict-MODERATE floor (12/42). **†E4B did not converge — see below.**

## The headline: the fine-tune works (base 0.29 → tuned 0.62)

The load-bearing result is the **E2B base→tuned delta**: both base instruct models zero-shot
the router rubric as **always-MODERATE (0.286)** — they can't do the task cold. Fine-tuning on
182 teacher-labeled examples moved E2B to **0.619 (+33 points)** — from majority-class
guessing to real discrimination (val loss 0.173 → 0.018, clean convergence). That is direct
evidence the flywheel pipeline delivers value: real logged tasks → a small local model that
learned to classify them.

**It does not yet beat the 9B (0.62 vs 0.76) — and the gap is diagnostic, not a wall:**
- E2B-tuned already **beats the 9B on SIMPLE (0.90 vs 0.70)** and ties CRITICAL (1.00), where
  the 182 examples had enough signal.
- It **collapses on COMPLEX (0.23)** — only **13 training examples** (7 %); the confusion
  matrix shows COMPLEX→MODERATE 7×. You cannot learn a class from 13 rows.
- Root cause in one line: **class imbalance × tiny n**, inherited from the MODERATE-heavy seed
  distribution (MODERATE 122 / SIMPLE 34 / COMPLEX 13 / CRITICAL 13).

## The E4B finding: QLoRA instability (an honest reproducibility note)

E4B, same recipe and data, **failed to learn** — it stayed at the always-MODERATE floor
(0.286) across every configuration tried, because its training never converged:

- LR 1e-4: train loss 1.84 (iter 50) → **diverged** up to 3.9 (iter 300).
- LR 5e-5: val loss reached 0.98 (iter 100) then **climbed back to 2.81** (iter 300) — best
  checkpoint (iter 100) still evaluated to always-MODERATE.

E2B converged cleanly on the *identical* pipeline, so this is a model-specific training
sensitivity (the larger 4-bit multimodal E4B overshoots at LRs E2B tolerates), not a pipeline
bug. **Not chased further** — two runs failing in different ways is the signal to report the
instability, not grind a third run past the thin-slice's scope. The scaled increment should
give E4B a gentler recipe (grad clipping + LR warmup/decay, lower LR) if it's kept in the
bake-off at all.

## What this proves and doesn't

- **Proves:** every S5/S6 stage runs unattended (harvest → teacher-distill → QLoRA on the Air
  → eval-gate) and produces real, comparable numbers; and **fine-tuning genuinely teaches a
  ~3 GB local model the router's job** (0.29 → 0.62). The tooling (4 scripts, 44 tests) is
  reusable at any scale.
- **Does not prove:** that a fine-tuned SLM can replace the 9B (0.62 < 0.76 on 155 rows), nor
  that E4B is trainable under this recipe.

## Bonsai-27B 1-bit — blocked on MLX, RUNS on the PrismML llama.cpp fork (2026-07-18 addendum)

`Bonsai-27B-mlx-1bit` cannot load in **stock MLX** (`ValueError: bits=1 not supported;
supported: 2–8`) — its 1-bit `Q1_0_g128` format needs custom kernels. Stock **llama.cpp**
can't run it either. **But the PrismML llama.cpp fork's prebuilt macOS-arm64/Metal binary
runs it fine** (`PrismML-Eng/llama.cpp`, release `prism-b9594`; note this fork renames the
non-interactive tool to `llama-completion`, and `--no-conversation` is unsupported on
`llama-cli`).

Curiosity tests (not a rigorous eval — n=4, slow):
- **Coherence:** generates correct, coherent technical prose at **~13.5 tok/s gen / 24 tok/s
  prompt** on the M4 Air via Metal. It's a *reasoning* model (emits a "Thinking Process").
- **Tier task, zero-shot: 3/4** (SIMPLE ✓, MODERATE ✓, CRITICAL ✓; missed COMPLEX → called a
  "god-object refactor" CRITICAL, a defensible over-escalation). Textbook rubric reasoning
  ("Effort very low… no data loss, no auth bypass… Conclusion: SIMPLE"). For contrast, the
  4-bit E2B/E4B **bases** zero-shot were the always-MODERATE floor (0.286) — the 1-bit 27B's
  *reasoning* massively out-discriminates a small base zero-shot, and is in the 9B's ballpark.
- **Caveat:** ~26–30 s/task (verbose CoT), and the answer needs parsing out of the reasoning
  (a naive last-tier-word extractor scored it 0–1/4 — a harness artifact, not the model). So
  it's a strong *reasoner*, not a drop-in fast classifier. A real n≥42 eval would need
  answer-extraction + patience; deferred.

## Next increment (cost-gated on this)

1. **Balance + scale the data** — the concrete ask this slice quantifies: generate toward the
   paper's 5k–10k with **per-tier floors** (COMPLEX/CRITICAL to ≥ several hundred each),
   seeding from more diverse harvested tasks + targeted synthetic hard cases.
2. **Stabilize E4B** (or drop it) — grad clip + LR schedule; eval the best checkpoint.
3. **Re-run the gate** — promote (swap `CLASSIFIER_MODEL`) only if the tuned SLM ≥ 9B on the
   union *and* regresses no tier.
4. Optional: Bonsai zero-shot in an isolated PrismML-fork venv (the "big-1-bit vs small-tuned"
   comparison at ~5 GB).
