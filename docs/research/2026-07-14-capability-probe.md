# Capability probe: testing the Routing-SOL success-transfer assumption

**Date:** 2026-07-14 · **Branch:** `feat/capability-probe` (stacked on `feat/routing-sol-eval`) · **Probe model:** Qwen3.5-9B-MLX-4bit on oMLX (M4 Air)

## 1. Question

The Routing-SOL hybrid bound says $747.11 (66.0%) of labeled imputed spend is
addressable — **assuming** every confidently-classified frontier success would
still have succeeded on its tier's cheap chain-lead. This probe tests that
assumption the only way available at solo-dev scale: replay each over-routed
session's originating task **single-shot** against a local-tier model and
grade the responses.

## 2. Method

`evals/capability_probe.py` selects the top over-routed sessions (hybrid
confidence — the same selection the SOL bound claims), prompts the local model
for a concrete solution (files + approach + key code), and records responses
for **separate, non-automated grading** (rubric below). Probe model is the
on-device 9B because the homelab 35B chain-lead was unreachable — a **weaker
proxy, so passes are conservative evidence; failures are inconclusive for the
35B.**

Rubric: **pass** = correct approach, plausible files/APIs, key change
essentially right · **partial** = right direction, wrong/missing specifics ·
**fail** = wrong frame or vacuous. Weights 1.0 / 0.5 / 0.0. Grader:
claude-fable-5, single grader (disclosed limitation). Grades:
`evals/probe_results/2026-07-14-grades.json` (gitignored with the responses —
they contain session text).

## 3. Results (8 sessions, $741.77 at stake, mean 32.7s/response)

| Tier | Savings | Grade | One-line rationale |
|---|---|---|---|
| COMPLEX | $571.24 | **partial** | Right improvement genre (async/queue/progress), hallucinated repo structure |
| SIMPLE | $66.26 | fail* | *Instrumentation artifact: probe prompt mis-framed a sysadmin question as iOS coding* |
| CRITICAL | $33.47 | fail | Invented feature work; no contact with real deployment needs |
| CRITICAL | $26.67 | **partial** | Directionally matches the real hardening track |
| COMPLEX | $16.65 | fail | Audit task requires repo access; single-shot cannot do it (model said so, then confabulated) |
| SIMPLE | $12.23 | **pass** | Named the real `PlayerManager.swift`, correct pre-resolve design |
| SIMPLE | $11.79 | **partial** | Workable research-script approach |
| SIMPLE | $3.45 | fail* | *Same framing artifact (how-to question answered as feature build)* |

**Expected savings (grade-weighted):**

- Local-tier pool (SIMPLE+COMPLEX, chain-lead = free local 35B): **$303.75 of
  $681.63 → 44.6%** of the pool, ≈ **27% of total labeled spend** — roughly
  **half the 66% SOL ceiling survives the probe**.
- CRITICAL rows ($60.14) are excluded from the local claim: their chain-lead
  is minimax-m3 (paid cloud), which a 9B probe cannot test.

## 4. The finding that matters most: concentration risk

One session carries **77% of the pool** ($571.24). The estimate is hostage to
its grade: pass → $583 (86%), fail → $18 (3%), partial (assigned) → $304
(45%). A single-shot grade on one mega-session is the weakest link in the
chain — **the next probe iteration should multi-shot that session type** (and
its cluster, once S3 clustering exists) rather than widening n indiscriminately.

Secondary finding: 2 of 4 fails are **instrumentation artifacts** — the probe
prompt hard-frames every task as iOS coding, which derailed two non-coding
tasks (a sysadmin question, a how-to). Probe v2 should frame the prompt from
the task itself. Removing artifact rows entirely: $303.75 of $611.92 → 49.6%.

## 5. Limitations

- Single-shot ≠ agentic session: real routed sessions have repo access and
  tools; two fails (audit, improvements-sweep) reflect that gap more than
  model capability.
- 9B proxy for the 35B chain-lead: conservative for passes, inconclusive for
  fails.
- n=8, single grader, one probe model, imputed economics throughout.

## 6. Next

1. Probe v2: task-adaptive prompt framing; multi-shot the $571 session type.
2. Rerun against the homelab 35B when reachable (the actual chain-lead).
3. Wire probe grades back into the SOL report as a `probe_adjusted_headroom`
   so dashboard consumers see ceiling AND estimate side by side.
