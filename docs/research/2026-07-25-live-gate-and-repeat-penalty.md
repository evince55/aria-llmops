# The live gate — one flag, a stronger incumbent, and a shrunken prize

**Date:** 2026-07-25 · **Closes:** the PROVISIONAL stamp on S7's promote
**Verdict: `e2b_v3_rescue` PROMOTES against a MEASURED incumbent.** The baseline is re-pinned.

For two days every self-hosted 9B served token-salad, and three increments were shipped with a
**replayed** incumbent and a PROVISIONAL stamp. The endpoint was never broken.

## The cause: `--repeat-penalty 0`

```
llama-server -m ornith_9b.gguf -c 32768 --jinja --repeat-penalty 0 ...
```

llama.cpp treats `1.0` as *no penalty*; `0` is a degenerate divisor that corrupts the logits as
the repeat window (`repeat_last_n 64`) fills. Same prompt, same server, same weights:

| `repeat_penalty` | `"Paris is the capital of"` |
|---|---|
| `0.0` (as launched) | `'?/n\nNew South Walcs /ns/walee <think>Thinking Properties/////'` |
| `1.0` | `' the country of . It is the largest city in the country and the most populous city…'` |

It explains every symptom: coherent-for-a-few-tokens-then-collapse (the window fills),
topically-correct words in scrambled order (logits corrupted, embeddings fine), both
`9b_mythos_q4` and `ornith_9b` failing identically (same server config), and the spurious
Chinese.

**Hypotheses tested and refuted first** — recorded because the wrong ones cost real time:
RoPE/context over-extension (`n_ctx 32768` < `n_ctx_train 262144`, so serving *below* the
training context — safe); chat-template mismatch (a bare `/v1/completions` continuation with no
template also failed); quantization (two different quants failed identically); tokenizer/vocab
mismatch (encode→decode round-trips clean); temperature (0 / 0.7 / 1.0 all failed); a stray LoRA
(`/lora-adapters` empty). The earlier advice to re-convert the GGUF was wrong.

## The live gate (n=176, incumbent measured, not replayed)

| config | acc | CRITICAL | COMPLEX | MODERATE | SIMPLE | verdict |
|---|---|---|---|---|---|---|
| incumbent (keyword + ornith_9b) | 0.761 | 0.931 | 0.711 | 0.617 | 0.878 | — |
| e2b_v3_standalone | 0.801 | 0.862 | 0.816 | 0.783 | 0.776 | REJECT (CRIT −0.069, SIMP −0.102) |
| **e2b_v3_rescue** | 0.761 | 0.931 | 0.789 | 0.600 | 0.837 | **PROMOTE** |

## The incumbent got stronger, so the bar moved

The pin recorded `9b_mythos_q8`; what is served is `ornith_9b`, and it is a better classifier:

| | 9b_mythos_q8 (replayed) | ornith_9b (measured) |
|---|---|---|
| accuracy | 0.705 | **0.761** (+0.056) |
| MODERATE | 0.417 | **0.617** (+0.200) |
| COMPLEX | 0.763 | 0.711 (−0.052) |

**Re-pinned** to the live numbers by owner decision. Consequences, stated plainly:

1. **`e2b_v3_rescue` now TIES rather than beats** (0.761 vs 0.761). It still promotes — no tier
   regression, and 3.2 GB against 5.8 GB — but its case is now **size, not accuracy**.
2. **The MODERATE prize shrank by three quarters.** The contested guard was reported as
   MODERATE +0.266 against a 0.417 incumbent; against this one it is 0.617 → 0.683 = **+0.066**.
   The keyword-preemption diagnosis still holds — the keyword/rescue split is identical (103/73),
   so the entire incumbent gain came from the 73 *rescue* rows getting a better model, and the
   103 preempted rows are still misclassified — but the guard is now a marginal call, not an
   obvious win. It remains REJECTED on SIMPLE −0.062 against the new pin, unchanged, because
   SIMPLE is 0.878 under both incumbents.
3. **Promotions recorded before today were judged against an easier reference.** Pinning the
   stronger configuration raises the bar going forward, which is the honest direction.

## Status after this run

- S7's promote: **confirmed live, PROVISIONAL dropped.**
- Baseline: **measured, not replayed** (`_provisional: false`), superseded entry retained.
- Contested guard (`LLMOPS_KEYWORD_GUARD`): still off, still rejected, prize now much smaller —
  reconsider whether it is worth pursuing at +0.066 MODERATE for −0.062 SIMPLE.
