# Routing-SOL: an oracle bound on routing headroom

**Date:** 2026-07-14 · **Branch:** `feat/routing-sol-eval` · **Status:** measured live (keyword + hybrid modes, oMLX-served Qwen3.5-9B on the M4 Air)

## 1. Motivation

Two NVIDIA sources set the method for this note. The Nemotron-Labs-Diffusion
tech report computes a *speed-of-light* (SOL) bound — an achievable-by-definition
oracle — before optimizing its decoder, and reports the gap to it (their
finding: 76.5% headroom over the best practical strategy). The SLM-agents
position paper (arXiv 2506.02153) argues agentic systems should route most
invocations to small models and gather routing data from their own traffic.
This note applies the SOL pattern to **our router**: given outcome-labeled
sessions, how much could hindsight-optimal routing have saved?

## 2. Method

`evals/routing_sol_eval.py` replays outcome-labeled sessions from the telemetry
ledger under an oracle policy:

| Session | Oracle move | Bucket |
|---|---|---|
| success, confidently classified | reprice tokens at the tier's chain-lead list rate (local lead = $0) | `over_routing_usd` |
| success, defaulted/unconfident | keep actual cost — **no claim** | `no_claim_usd` |
| failure that used a non-frontier model | reprice at `claude-opus-4-8` (escalate immediately) | `under_routing_usd` |
| failure entirely on frontier | keep actual cost — no better hindsight move | — |

Unlabeled sessions are excluded. `headroom_usd = actual − oracle` is a
**ceiling under stated assumptions**, not a promise. Confidence comes from
`classify_hybrid` — keyword-only by default; with `--model-classifier`, the
keyword-first + 9B-rescue hybrid (a model-rescued tier counts as confident,
matching live routing).

Run: `python3 telemetry.py eval sol [--model-classifier]`

## 3. Results — SOL on the 2026-07-14 ledger (13 labeled sessions, $1,132.23 imputed)

| Confidence signal | Oracle | **Headroom** | No-claim pool | Under-routing |
|---|---|---|---|---|
| keyword only | $1,120.00 | **$12.23 (1.1%)** | $760.37 (67%) | $0.00 |
| **hybrid (keyword + 9B rescue)** | $385.12 | **$747.11 (66.0%)** | **$0.00** | $0.00 |

Hybrid per-tier: SIMPLE 8 sessions $169.23 → $70.16; COMPLEX 2 / $587.89 → $0
(local chain-lead); CRITICAL 3 / $375.11 → $314.97 (paid-cloud reprice; includes
one frontier failure kept at actual). Zero classifier fallbacks — the 9B ran on
the Air (oMLX, dynamic memory guard) alongside Claude Desktop throughout.

**Findings.**
1. **Keyword-confident spend is already near-optimally routed** (1.1%): where
   the keyword fires, the router does its job; no observed failure is
   attributable to routing down (under-routing $0 in both modes).
2. **The binding constraint is coverage, and the 9B-rescue removes it**: the
   no-claim pool (67% of labeled spend) drops to zero — every previously
   shrugged session gets a confident tier — converting $747.11 (66%) into an
   addressable ceiling.
3. The heavier the rescue's role, the heavier the success-transfer assumption
   (§6): the hybrid bound assumes e.g. a $500 frontier COMPLEX success would
   still succeed on the local 35B. Treat 66% as the ceiling the capability
   probe (§7) must now test, not as expected savings.

## 4. Ablation — classifier strategies on the labeled sets (all rows live, 2026-07-14)

| Strategy | keyword_tuned (n=24) | prose_blind (n=18) | union (n=42) |
|---|---|---|---|
| default-MODERATE (floor) | 0.250 | 0.333 | 0.286 |
| keyword | 1.000 | 0.444 | 0.762 |
| 9B-primary | 0.750 | 0.556 | 0.667 |
| **hybrid** | **1.000** | **0.556** | **0.810** |

Reads exactly like an ablation should: keyword is perfect on its home
distribution and weak on prose (0.444 — up from 0.333 after the
severity-by-consequence CRITICAL patterns landed on main); 9B-primary
generalizes to prose but *under-rates* keyword-tuned severity rows (0.750 —
the documented blind spots); the hybrid inherits the better half of each and
beats both components on the union (0.810 ≥ max(0.762, 0.667)), empirically
validating the keyword-first + rescue design.

## 5. Reproduction

```bash
export LLMOPS_CLASSIFIER_BASE_URL=http://127.0.0.1:8000/v1   # oMLX on the Air
export LLMOPS_CLASSIFIER_MODEL=Qwen3.5-9B-MLX-4bit
export LLMOPS_CLASSIFIER_API_KEY=<key from ~/.omlx/settings.json>
python3 telemetry.py eval sol                    # keyword-confidence SOL
python3 telemetry.py eval sol --model-classifier # hybrid SOL
python3 -c "from evals.classifier_comparison import main; main()"
```

Fallback tier if the 9B is memory-blocked (16GB Air under load): set
`LLMOPS_CLASSIFIER_MODEL=gemma-4-e4b-it-4bit` (5.0GB, on-disk) — not needed in
this run (72% free memory, zero fallbacks).

## 6. Limitations

- n=13 labeled sessions; treat all dollar figures as directional.
- `imputed_usd` is list-rate imputation under a subscription — headroom is
  imputed economics, not cash flow.
- **The success-transfer assumption dominates the hybrid bound**: it assumes a
  confidently-classified success would still succeed on the tier's chain-lead
  model. Untested counterfactually; the 66% is a ceiling.
- Outcome labels come from the heuristic/9B grader; 8 unlabeled sessions are
  excluded, not assumed.

## 7. Next

1. **Local-model capability probe** on the top over-routed sessions: actually
   run a sample on the local 35B / cheap cloud tier and measure success — this
   converts the 66% ceiling into a defensible expected-savings number (the
   NVIDIA move: ceiling first, then close the gap).
2. Grow n via the claude-code route-logging hook (live since 2026-07-12) +
   outcome backfill.
3. S3 task clustering over harvested prompts (CLIMB-style) so SOL can be
   reported per-cluster and the over-routed pool localized.
