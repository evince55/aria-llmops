# A1 — the input/output token split, and why A2 can't be an A/B yet

**Date:** 2026-07-25 · **Implements:** A1 from `2026-07-18-output-brevity-caveman.md`
**Status:** shipped. The measurement exists; the data to *use* it does not, and that is the
finding.

A1's brief was one sentence: *"Add per-task, per-model tracking of output tokens vs input
tokens, and surface output-tokens-per-task in the dashboard. Without this, A2 and A3 are vibes.
With it, they're A/Bs."*

## What shipped

`telemetry/token_split.py` — `split_totals`, `by_model`, `per_session_output`,
`measured_output_ratio` — plus a dashboard section surfacing all of it. 20 tests.

The dashboard previously showed **cost** by model but no token counts at all, so there was no
way to see whether verbosity was what you were paying for.

## The trap that shaped the design: which denominator?

Under prompt caching, "output ratio" is meaningless until you say what it is a ratio *of*. This
repo's own ledger:

| | tokens |
|---|---|
| input | 874,069 |
| output | 6,510,465 |
| cache write | 70,175,560 |
| **cache read** | **2,300,783,990** |

```
out / (in + out)            = 0.882
out / (in + out + cache)    = 0.003
```

**Same events, 300× apart.** So both are computed, both are always labelled, and they are never
averaged or silently swapped. `measured_output_ratio` uses the **cache-exclusive** one on
purpose: cached input is billed on its own line and is driven by prompt *reuse*, so mixing it in
would make a verbosity metric track caching behaviour instead.

I nearly shipped a headline "cost is understated by 40%" off the 0.882 figure before checking
what was in the denominator. It isn't a defensible claim — see below.

## The finding: there is no routed-call telemetry to measure

`CostMonitor.estimate_cost` prices every route from an **assumed** 40% output ratio, and that
number drives both the reported per-task cost and — through `should_route_to_local()` — when the
budget gate fires. A1 was supposed to replace it with a measured value. It cannot, yet:

- Of 5,692 usage events, essentially all are **ingested Claude Code sessions**, not router calls.
- `llama-cpp/qwen35b` has **~74 tokens** logged in total.
- `opencode-go/minimax-m3` has **none** — because `ModelRouter.run_task` only *executes* models
  whose name starts with `llama-cpp`. Cloud routes are decided and priced but never run through
  the router, so they are never instrumented by it.

So the 0.882/0.003 figures describe the *harness*, not the *router*, and extrapolating either
onto a minimax-m3 route would be exactly the kind of unvalidated constant A1 exists to remove.
**The assumption is therefore left at 0.4** — now a named, documented constant
(`llmops.DEFAULT_OUTPUT_RATIO`) rather than a magic number, with a test pinning it against the
telemetry-side copy so the two cannot drift.

**This directly changes A2's plan.** A2 (terse-output A/B on the local `build` agent) is
measured against A1's split on the local route — the route with 74 tokens of history. Running
the A/B today would compare two arms with no statistical content. A2 needs local-route traffic
first, or it will produce exactly the vibes A1 was meant to eliminate.

## What the dashboard now shows

- Totals for input / output / cache-read, with **both** ratio definitions side by side and a
  note on which one a brevity change actually moves.
- **Output tokens per session** — median *and* mean, because one runaway agentic session
  otherwise sets the headline. ("Per task" is the brief; session is the finest grouping the
  events carry — there is no `task_id` — so it is named for what it is.)
- A per-model table: input, output, cache-read, both ratios, event count.

Still self-contained: no CDN, no external fetch, inline SVG only.

## Next

1. **Generate local-route traffic** (or instrument cloud routes) so `measured_output_ratio` has
   something to measure. This is the precondition for A2, not an optional extra.
2. Then A2 as a real A/B: brevity clause on the local `build` agent, measured on
   output-tokens-per-task + wall-clock, killed if the eval gate shows a quality regression.
3. A3 (0.6× brevity cap in the distill filter) is independent of this and still stands.
