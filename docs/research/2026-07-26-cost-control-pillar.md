# Cost control — the pillar was pointed at the wrong thing

**Date:** 2026-07-26 · **Pillar:** "cost-per-task control", the project's second stated goal
**Finding: routed spend is ~$0 by construction. The real cost centre is the EVAL LOOP, and it
was completely uninstrumented.**

## What the data said before anything was built

| | |
|---|---|
| route decisions recorded | 218 |
| **chose the free local model** | **213 (97.7%)** → `estimated_usd = 0.0` |
| chose a cloud model | 5 — never *executed*, so no actual cost exists |
| decisions joinable to an actual | **3 of 218 (1.4%)** |

The pillar was not merely unvalidated, it was **unexercised**. `CostMonitor.estimate_cost`
multiplies an unvalidated 0.4 output ratio by a $0 rate; `should_route_to_local()` has never had
anything to gate. `TIER_PREFERENCE` sends everything except CRITICAL to `llama-cpp` first, and
CRITICAL is rare, so the router is doing exactly what it was configured to do.

## Then the subscription hit 100%

Mid-session, the opencode-go rolling limit hit **100%** (monthly 67%). The daily bar for
2026-07-25: deepseek-v4-pro **$1.94** + glm-5.2 **$4.75** + minimax-m3 **$0.28** ≈ **$7 in one
day** — almost entirely this project's own **A/B grading**. The 300 s judge timeouts that had
been degrading the A2c run were a symptom of that, not of long prompts.

**So the money never went where the cost model was looking.** Routed inference is free by design;
evaluation is what costs. And `evals.judge_labels.call_judge` shelled out to `opencode run` and
recorded **nothing** — the only cost that mattered was the only one nobody could see.

## What shipped

`telemetry/cost_control.py`:

* **`counterfactual()`** — what a workload *would* have cost on a priced model, computed from
  **measured** tokens, never an assumed split. That is the number that justifies local-first, and
  nothing computed it before.
* **`savings_report(..., harnesses=("llmops-local",))`** — scoped to work the router actually
  executed. Unscoped, the ledger's ingested Claude Code sessions dominate and the report reads
  "$0.00 saved", hiding the result entirely.
* **`estimate_error()`** — the feedback loop, **with coverage attached**. On this ledger it joins
  3 of 218 decisions, so it reports `sufficient: false` rather than a confident mean over three
  rows.
* **`eval_spend()` + `judge_event()`** — every judge call is now logged. `opencode run` returns
  no usage block, so tokens are character-count **estimates**, labelled `estimated-from-chars`
  and never presentable as a bill.
* **Unpriced models are flagged, not zeroed.** The two graders that exhausted the subscription
  (`opencode-go/glm-5.2`, `deepseek-v4-pro`) have **no entry in `MODEL_RATES`**, so a naive lookup
  prices the most expensive activity in the project at $0.00. `eval_spend` counts them separately
  and states that its total is a **floor, not the bill**.

Also fixed, earned the hard way: `grade_ab.py` now **checkpoints each row**. Roughly 50 minutes of
grading was lost when the A2c run had to be killed, because results were serialised only at the
end.

## The transferable lesson

**Instrument the loop you actually spend on, not the one your architecture is about.** This
project's identity is "cheap routing", so the cost model was built around routing — which costs
nothing, because that is the whole point of routing locally. The spend was in the *measurement*
of the router, a category the cost model had no concept of. A cost model that cannot see the
line item that exhausts your budget is not a cost model.

## Honest state of the pillar

- **Routed spend: measured, and it is ~$0.** The counterfactual on 338 router-executed tasks is
  $0.083 on minimax-m3 ($0.00025/task) versus $0 actual.
- **Eval spend: now visible, but only prospectively** — instrumentation logs from here on, and the
  ~$7 already spent is not reconstructable from the ledger.
- **Estimate accuracy: still unknown**, and honestly reported as such (1.4% coverage).
- **Missing:** rates for the grader models, so eval cost is a floor. Adding them is a one-line
  change per model once the published prices are to hand.
