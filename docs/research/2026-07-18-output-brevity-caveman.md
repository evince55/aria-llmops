# Output-token brevity — what the "caveman" ecosystem teaches aria-llmops

**Date:** 2026-07-18 · **Status:** research → 3 proposed adoptions · **Author:** Claude Code (investigation session)

## TL;DR

The viral [caveman](https://github.com/JuliusBrussee/caveman) family (90k+ stars) is one
idea — make the agent's **output** terse — packaged five ways. We should not install any
of it. We should adopt the ideology on three fronts where it composes with what we
already have:

1. **Telemetry:** track the output/input token split per task and per model.
2. **Local build agent:** terse-output system prompt — the win is *latency* on
   throughput-bound local models, not dollars.
3. **Flywheel distillation:** add a brevity cap to the teacher-output data filter so the
   student bakes in terseness at zero inference-time cost. This is the real prize, and
   cavegemma's documented filter bug tells us exactly what to avoid.

Headroom (already integrated, see `evals/headroom-eval.md` in the Aria repo) compresses
**input** (tool outputs / build logs). Caveman-style brevity compresses **output**
(agent prose). Orthogonal axes; they stack.

## The source material, skeptically

| Repo | What it is | Our verdict |
|---|---|---|
| `caveman` (90k★) | System-prompt skill: answer tersely. 65% output cut on chatty prompts. | Skip the install; take the ideology. |
| `cavemem` (626★) | Compressed cross-agent memory (SQLite + FTS5 + MCP). | Skip — graphify + Obsidian vault fill this role. |
| `cavekit` (1.1k★) | Compressed spec-driven dev loop. | Skip — covered by existing workflow tooling. |
| `caveman-code` (793★) | A whole terminal agent, terse end-to-end. | Skip — not switching agents. |
| `cavegemma` (96★) | Gemma 4 31B LoRA that speaks tersely *natively*. 27% output cut baked into weights. | **Steal the method + the failure lesson** (below). |

Unusual credit: the author's own
[`docs/HONEST-NUMBERS.md`](https://github.com/JuliusBrussee/caveman/blob/main/docs/HONEST-NUMBERS.md)
corrects the marketing. Key admissions, all of which match our priors:

- The skill **adds ~1–1.5k input tokens per turn** (injected rules).
- Real **session-level** savings are **14–21%** on output-heavy workloads, and
  **net-negative on terse coding Q&A** — because in agentic coding, input tokens dwarf
  output tokens. (Their issue #145 measured exactly this.)
- Per-request-billed agents (Copilot credits) save nothing from shorter answers.

Rule of thumb from their own doc: replies under ~1.5–2k output tokens → the skill costs
more than it saves. Our coding agents' replies are mostly diffs and tool calls — terse
already. Hence: no install.

## Why the ideology still matters for *this* stack

Our fleet is unusual in one way that flips the economics: **local models are
throughput-bound**. On qwen35b (homelab gfx1102) or the oMLX cascade (M4 Air), every
output token costs wall-clock time, not fractions of a cent. Output brevity on local
models is a **latency** lever — a shorter answer is a faster loop, every turn, for free.
Cloud-side (minimax-m3 per-token billed), output tokens are also the expensive ones
per-token; smaller effect, right direction.

There is also an accuracy angle worth an eval: the paper caveman cites —
[*Brevity Constraints Reverse Performance Hierarchies in Language Models*](https://arxiv.org/abs/2604.00025)
(March 2026, 31 models) — found brevity constraints **improved** accuracy on some
benchmarks. Treat as a hypothesis to test in our harness, not a fact.

## Proposed adoptions

### A1 — Telemetry: output/input split (small; do first)

Add per-task, per-model tracking of output tokens vs input tokens to the telemetry
layer, and surface output-tokens-per-task in the dashboard. Without this, A2 and A3 are
vibes. With it, they're A/Bs.

### A2 — Terse-output style prompt on the local `build` agent (trivial; A/B it)

Add a brevity clause to the opencode `build` agent's system prompt (local qwen35b
route): no preamble, no restating the task, answer-first, code/paths/errors byte-exact.
Measure with A1's split + wall-clock per task. Expected effect: modest token cut,
noticeable latency cut on local inference. Kill it if the A/B shows quality regression
in the eval gate.

### A3 — Brevity cap in the flywheel distillation filter (one-line-ish; the prize)

Maps directly onto the S5 pipeline (teacher-distill → QLoRA-on-Air → eval-gate,
`2026-07-17-s5-thin-slice-results.md`). Cavegemma's documented failure is the
instructive part: their data filter **accepted rewrites up to 1.0× source length**, so
the model learned to sit at the ceiling — debug/refactor categories saved only 8% vs
41% for dialogue. They call it "a filter bug, not a model limit."

For the scaled flywheel run: filter teacher outputs to **≤ ~0.6× verbose-source
length**, with code fences byte-exact, before they enter the training set. The student
then speaks tersely natively — zero per-turn prompt overhead, and the terseness
compounds with A2's latency win. The existing eval gate already protects against
quality loss (cavegemma held 0.91–0.98 semantic cosine and 96–100% byte-exact fences
through the same kind of squeeze).

### Deferred — compressing AGENTS.md / CLAUDE.md

`caveman-compress` measured ~46% input reduction on memory files, paid every session.
Tempting, but AGENTS.md is the cross-agent *contract* — two different model families
must parse it reliably, and a misread convention costs more than the tokens save.
Revisit only with a comprehension eval; if ever done, compress low-stakes sections
only, conventions verbatim.

## Non-goals

- Installing caveman/cavemem/cavekit/caveman-code in any harness.
- Applying brevity style to cloud *plan/debug* agents (reasoning-heavy; brevity
  pressure on planning output is exactly where we'd expect silent quality loss —
  don't touch without an eval).
