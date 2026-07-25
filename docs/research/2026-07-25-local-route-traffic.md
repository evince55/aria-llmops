# Local-route traffic — the 0.4 assumption measured, and A2 answered

**Date:** 2026-07-25 · **Follows:** `2026-07-25-a1-token-split.md`
**Status:** traffic generated, assumption measured. A2's token/latency arm is answered; its
eval-gate quality arm is not.

A1 shipped the split and found nothing to measure: `llama-cpp/*` had ~74 tokens logged and the
cloud routes are never executed by `run_task`. This generates real routed-call traffic through
the production path so `measured_output_ratio` has evidence.

## Setup, stated plainly

Nothing was serving — homelab, Windows box and localhost were all down — so the local route was
pointed at **`mlx_lm server` on this Mac** running `gemma-4-e4b-it-4bit` (23 tok/s).

**The route's identity and the served weights differ, and that matters when reading these
numbers.** `TIER_PREFERENCE` names the local route `llama-cpp/qwen35b`, and that is the name the
router prices, executes and logs under; the model actually answering was E4B. So the ratio below
is a property of *this workload on a local instruct model*, not a measurement of qwen35b. The
`gemma-4-12b-coder` model would have been the better analogue but its `gemma4_unified` type is
unsupported by mlx-lm 0.31.3.

24 representative SIMPLE/MODERATE tasks (written for this purpose, not drawn from the quarantined
classification datasets), run through `ModelRouter.run_task` with `log_usage=True`.

## The assumption was wrong by 141%

| | measured | assumed |
|---|---|---|
| output ratio (cache-exclusive), local route | **0.962** | 0.400 |

`CostMonitor.estimate_cost` prices every route off that 0.4. For this route the dollar impact is
nil — `llama-cpp/*` is rated $0/$0 — but the number also drives `should_route_to_local()`, and it
is now demonstrably wrong by 141% for short-prompt/long-generation agentic coding work, which is
exactly the shape of the cloud routes it *does* price. It stays at 0.4 until there is
routed-call evidence from a cloud model; this is the first real datapoint against it.

## A2's token and latency arms: a large win

Same 24 tasks, the only difference being A2's brevity clause prepended.

| metric | baseline | terse | delta |
|---|---|---|---|
| output tokens | 17,030 | 5,477 | **−68%** |
| mean output/task | 709.6 | 228.2 | −68% |
| wall clock | 695.5 s | 227.5 s | **−67%** (3.05× faster) |
| mean per task | 28.98 s | 9.48 s | −67% |
| input tokens | 667 | 1,531 | +130% (the clause) |
| **total tokens** | **17,697** | **7,008** | **−60%** |
| truncated at the 800 cap | **16 / 24** | **1 / 24** | — |

The research doc predicted "modest token cut, noticeable latency cut". The token cut is not
modest, and the latency cut is the headline: on throughput-bound local inference, output tokens
*are* wall clock.

## Quality moved the same direction, which was not the expected risk

The truncation row is the tell: **the baseline hit the token cap on two thirds of tasks** — those
answers were cut off mid-generation. Spot-checking identical tasks in both arms:

| task | baseline | terse |
|---|---|---|
| "chunk a list into batches of n" | 800 tok, truncated mid-sentence after deciding *"which language you prefer"* — **never produced the function** | 46 tok, correct working code |
| "regex for a semantic version" | 701 tok — correct regex, then a long explanatory table | 15 tok, the same correct regex |

So on this evidence the brevity clause does not trade quality for tokens; it *raises* completion
rate, because the baseline spends its budget on preamble and runs out. That is the opposite of
the failure A2 was told to watch for ("kill it if the eval gate shows quality regression").

**This is not the eval gate.** Two spot-checked tasks and a truncation count are a strong signal,
not a verdict. A2 remains open until the brevity clause is graded through the eval gate proper.

## Caveats worth carrying

- **Route identity ≠ served model** (above). Do not quote 0.962 as "qwen35b".
- The 800-token cap censors the baseline arm: its true output ratio and mean are *higher* than
  measured, so the −68% understates the effect.
- 24 tasks, one model, one workload shape. Enough to kill a 0.4 assumption for this route; not
  enough to re-price cloud models.
- Local inference is free, so none of this is a dollar saving — it is a **latency** saving, which
  is the currency that matters on a throughput-bound local route.

## What this unblocks

`evals/local_traffic.py` (+10 tests) regenerates either arm on demand, so the measurement is
repeatable rather than a one-off. A2's remaining work is the quality gate, not the plumbing.
