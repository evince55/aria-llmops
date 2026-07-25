# S8 — shipping the promoted adapter, and making degradation loud

**Date:** 2026-07-25 · **Follows:** `2026-07-22-s7-complex-regen-gate-results.md`
**Status:** deployed behind an opt-in flag, parity-verified. Default unchanged.

S7 promoted `e2b_v3` and the flywheel stopped there — a promote nothing runs is a
research result, not an LLMOps outcome. S8 does the two things that turn it into one:
serve the promoted model, and make sure the serving layer tells you when it breaks.

## Why the second half exists: a real incident

On 2026-07-23 the self-hosted 9B was **reachable and broken** — it answered every
request with token-salad (`"The capital of France is"` → `" an and Paris theAnd in
that it will not"`), across two quants, three temperatures, and both the chat and raw
completion endpoints.

`ModelClassifier.classify` handled that the worst possible way: no exception was
raised, so no `except` fired; no tier appeared in the reply, so it fell through to
the keyword classifier and returned a confident-looking answer. **Production routing
silently reverted to keyword-only.** Only the *unreachable* path logged anything; the
*reachable-but-useless* path was completely silent.

That is the same failure class the eval harness has now hit four times (Bonsai's false
0/4, the MLX 8-token floor, the outcome-grader phantoms, this) — and the first time it
reached the production path.

### What changed

* Every fallback now logs **with the offending reply** — `"model classifier no tier in
  reply ';; the\\n: to::'"`. Without the text you cannot tell *model is broken* from
  *model disagreed*; characterising the incident took several manual probes.
* `stats` / `fallback_rate()` make degradation **measurable**, not just visible.
* Sustained fallback raises an alarm:
  `classifier DEGRADED: 3 consecutive keyword fallbacks (fallback rate 100%) —
  routing is keyword-only`. Fires **once** per outage (a broken endpoint serves
  thousands of tasks), and **re-arms on recovery** so a second outage still reports.
* One fallback never alarms — models legitimately answer off-rubric. Only a collapse does.

**Verified against the live broken endpoint**, not just in tests: the alarm fired on
the third consecutive fallback. The same run before this change printed nothing.

## Deploying the promoted adapter

The router could only reach a classifier over HTTP (`LocalLlamaClient`), so the
promoted model had nowhere to run. `mlx_classifier.MLXClassifierClient` implements the
same duck type against a local MLX model + LoRA adapter.

```bash
LLMOPS_CLASSIFIER_BACKEND=mlx    # default stays "http" — opt-in, nothing changes for existing deployments
LLMOPS_MLX_BASE=...              # both default to the S7 base + e2b_v3 adapter
LLMOPS_MLX_ADAPTER=...
```

Three properties carry the risk, and each is pinned by tests:

1. **Chat-template parity.** The adapter was fine-tuned chat-templated; serving it a
   raw string is train/serve skew that costs accuracy with no visible symptom.
2. **Raw text out, never a mapped tier.** `classify_finetuned.map_tier` resolves
   anything unparseable to MODERATE. Mapping inside the client would make a broken
   model look like a confident MODERATE answer — re-creating the always-MODERATE
   artifact *and blinding the alarm above*. Tier parsing stays with the caller.
3. **Lazy load.** The model loads on first use, not at construction, so keyword-only
   invocations (and `--help`) never pay 3.2 GB.

`llmops.py` remains **stdlib-only at runtime**: the mlx import lives inside the
backend branch and inside the client's own seams.

## Deployment parity — the control that matters

A deployment is only trustworthy if it reproduces what the gate measured. The same 176
human-written rows, through the **production** `classify_hybrid` path:

| | Accuracy | CRITICAL | COMPLEX | MODERATE | SIMPLE |
|---|---|---|---|---|---|
| Gate recorded (`e2b_v3_rescue`) | 0.761 | 0.931 | 0.789 | 0.600 | 0.837 |
| **Deployed** (`classify_hybrid`, mlx backend) | **0.761** | **0.931** | **0.789** | **0.600** | **0.837** |

Identical on every tier to four decimals — so the chat template is right, the wiring is
faithful, and what the gate promoted is exactly what production runs. 176 rows in
**30.9 s**, no endpoint involved.

## What this buys

- **5.8 GB → 3.2 GB** for equal-or-better per-tier recall.
- **No remote endpoint** in the classification path — the single point of failure that
  took classification down on 2026-07-23 is gone for this backend.
- Silent degradation is no longer possible on either backend.

## Honest limits

- The S7 verdict remains **PROVISIONAL**: the incumbent arm was replayed, not measured,
  because no coherent 9B endpoint has been available. S8 changes nothing about that —
  re-run `promotion_gate.py` without `--incumbent-from` when one is.
- **The default is still `http`.** This ships the capability, not a cutover. Flipping
  the default is a separate decision that should wait for the live confirmation.
- MODERATE recall (0.600) is still the weakest tier and the next target.
- `mlx-lm` needs py3.10+; selecting the mlx backend on the 3.9 floor raises at import.
  That path degrades to keyword-only **loudly** now, which is the point.
