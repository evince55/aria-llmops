# Shipping the flywheel: the promoted adapter becomes the default classifier

**Date:** 2026-07-25 · **Follows:** `2026-07-25-live-gate-and-repeat-penalty.md`
**Change:** `LLMOPS_CLASSIFIER_BACKEND` now defaults to **auto** — mlx where the adapter exists,
http otherwise.

S5→S9 built a tuned 3.2 GB classifier, gated it, deployed it behind a flag, and confirmed it
live. It was still off by default, which meant none of that reached production. This turns it on.

## Why now — the evidence is complete

| | keyword + served 9B | keyword + e2b_v3 (mlx) |
|---|---|---|
| accuracy | 0.761 | **0.761** (tie) |
| CRITICAL | 0.931 | 0.931 |
| COMPLEX | 0.711 | **0.789** |
| MODERATE | **0.617** | 0.600 |
| SIMPLE | 0.878 | 0.837 |
| memory | 5.8 GB | **3.2 GB** |
| network | a served endpoint | **none** |

Measured live against a *measured* incumbent (not replayed), promoted with zero tier
regressions against both that incumbent and the pinned baseline.

**The case is operational, not accuracy.** It is a tie, and the incumbent is actually slightly
better on MODERATE. What tips it: half the memory, and no endpoint. This week the endpoint was
the single point of failure — one `--repeat-penalty 0` flag took classification down for two
days across two different models, and the code defaults were pointing at a model
(`9b_mythos_q8`) and host that were not being served. An in-process classifier has no such
surface.

## Why "auto" and not a flip

A blind flip would silently degrade every machine *without* the adapter — CI, a fresh clone, the
homelab — to keyword-only routing. So the default resolves from what is actually on disk:

* adapter present → **mlx**
* adapter absent → **http**
* `LLMOPS_CLASSIFIER_BACKEND=http|mlx` → honoured verbatim, always

An explicit `mlx` on a machine missing the adapter is deliberately *not* second-guessed: it fails
loudly at first use rather than quietly contradicting the operator. Unknown values fall through
to auto.

The check is injectable (`resolve_inference_config(env, _exists=...)`) so it is tested without
touching a filesystem, in both directions.

## Verified out of the box

With **no environment variables set at all**: backend resolves to `mlx`, the router builds an
`MLXClassifierClient`, and 4/4 tier cases classify correctly in 3.8 s — no endpoint involved.

461 tests green.

## Known gap left deliberately

The `http` fallback's defaults (`_DUAL_DEFAULTS` / `_SWAP_DEFAULTS`) still name
`9b_mythos_q8.gguf` and a LAN URL, while the box currently serves `ornith_9b`. That drift no
longer bites the default path, and picking the canonical served model is the owner's call, not a
guess to bake into a public repo — so it is flagged rather than silently changed. If ornith is
the long-term classifier, that is a one-line edit to `_DUAL_DEFAULTS`.
