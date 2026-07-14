# Routing-SOL: an oracle bound on routing headroom (baseline)

**Date:** 2026-07-14 · **Branch:** `feat/routing-sol-eval` · **Status:** baseline measured; hybrid variant blocked on hardware (see §5)

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
**ceiling under stated assumptions**, not a promise — its core assumption (a
confidently-cheap success stays a success on the cheap tier) is the same one
`routing_quality_eval`'s `strong_downgrade_candidates` already makes.

Run: `python3 telemetry.py eval sol` (add `--model-classifier` for the
keyword-first + 9B-rescue hybrid confidence).

## 3. Results — keyword-confidence SOL (2026-07-14 ledger)

| Metric | Value |
|---|---|
| labeled sessions | 13 (8 unlabeled excluded) |
| actual (imputed) | **$1,091.71** |
| oracle | $1,079.48 |
| **headroom** | **$12.23 (1.1%)** |
| over-routing pool | $12.23 |
| under-routing penalty | $0.00 |
| **no-claim pool** | **$719.84 (66% of labeled spend)** |

Per-tier: MODERATE 10 sessions / $779.26 (oracle = actual — nearly all
defaulted), SIMPLE 2 / $22.98 → $10.75, CRITICAL 1 / $289.47 (frontier
failure; no claim).

**Finding:** the router's binding constraint is **classifier coverage, not
accuracy**. Keyword-confident spend is nearly optimally routed already
(1.1% headroom). Two-thirds of labeled spend sits where the keyword classifier
can only shrug (defaulted MODERATE) — the oracle cannot claim it because no
confident signal exists. This is the same shape as the paper's sampler result:
the mechanism is fine; the *signal* leaves most of the ceiling unclaimed. The
9B-rescue exists precisely to convert this pool; the hybrid SOL (§5) measures
how much of the $719.84 it actually unlocks.

## 4. Ablation — classifier strategies on the labeled sets

Offline rows measured now; model rows from the repo's 2026-07-05 capability
probe (see `_classify` docstring), pending rerun on current hardware:

| Strategy | keyword_tuned (n=24) | prose_blind (n=18) | union (n=42) |
|---|---|---|---|
| default-MODERATE (floor) | 0.250 | 0.333 | 0.286 |
| keyword | 1.000 | **0.333** | 0.714 |
| 9B-primary *(2026-07-05 probe)* | ~0.92–0.96 | — | — |
| hybrid *(expected ≥ max of rows)* | *pending* | *pending* | *pending* |

Keyword per-tier (union): precision 1.000 on CRITICAL/COMPLEX/SIMPLE, recall
0.46–0.71; MODERATE precision 0.500 (the default dumping ground). On
prose-blind input, keyword scores exactly the always-MODERATE floor — zero
added value outside its keyword coverage. High precision, low coverage:
consistent with §3.

## 5. Blocked measurements (hardware)

The 9B classifier had no live endpoint at measurement time (external NVMe with
the MLX models disconnected; homelab llama.cpp unreachable). To complete:

```bash
export LLMOPS_CLASSIFIER_BASE_URL=http://127.0.0.1:8000/v1   # oMLX
export LLMOPS_CLASSIFIER_MODEL=Qwen3.5-9B-MLX-4bit
export LLMOPS_CLASSIFIER_API_KEY=<key from ~/.omlx/settings.json>
python3 telemetry.py eval sol --model-classifier      # hybrid SOL
python3 -c "from evals.classifier_comparison import main; main()"  # full ablation
```

## 6. Limitations

- n=13 labeled sessions; treat all dollar figures as directional.
- `imputed_usd` is list-rate imputation under a subscription — headroom is
  imputed economics, not cash flow.
- The oracle's success-transfer assumption is untested counterfactually; a
  local-model capability probe on the over-routed sessions would tighten it.
- Outcome labels come from the heuristic/9B grader (high precision, incomplete
  recall); unlabeled spend (8 sessions) is excluded, not assumed.

## 7. Next

1. Rerun §5 when a classifier endpoint is live → hybrid-SOL headroom.
2. Grow n via the claude-code route-logging hook (live since 2026-07-12) +
   outcome backfill.
3. S3 task clustering over harvested prompts (CLIMB-style) so per-cluster SOL
   can localize the no-claim pool.
