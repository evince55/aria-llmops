# From test bed to service: token-cost-conscious automation for businesses

**Date:** 2026-07-09 · **Status:** direction document (owner-approved goal,
agent-drafted plan) · **Prereq reading:** README, `calculator/savings_model.py`

## 1. The offering, one line

Automate a business's simple, repetitive, expensive task streams with an
agentic pipeline that **routes every model call to the cheapest tier that can
do the job**, measures itself end-to-end, and reports the client's actual
savings every month — against numbers we predicted before signing.

## 2. What this repo already proves (the rapport data)

| Claim in a sales conversation | Where it's proven here |
|---|---|
| "We meter every call and cost it honestly" | telemetry ledger: idempotent JSONL, imputed vs actual USD, reprice |
| "Tasks are classified and routed, not YOLO'd at a frontier model" | ModelRouter: keyword-first + 9B-rescue hybrid, cost gate, tier preferences |
| "Cheap/local execution really works, and we know its failure modes" | live A/B run: executions on a self-hosted 35B, graded outcomes, reproduced classifier blind spots |
| "We measure quality, not just cost" | outcomes (keyword + model grader) joined to spend in the quality eval |
| "We tell you when the fancy option isn't worth it" | calculator: recommends cloud-only below the local box's measured break-even |
| "You can audit everything" | every number in README/calculator carries provenance; evals are re-runnable |

The numbers are small-N and say so. That is a feature in the sales motion:
the pitch is *"here is my measured starting point and the machine that keeps
measuring,"* not *"trust my slide deck."*

## 3. The modular pipeline ("one design fits nearly all")

Every engagement instantiates the same six stages; only the two ends are
custom per client:

```
[1 connector] -> [2 normalizer] -> [3 router] -> [4 executors] -> [5 outcome] -> [6 ledger/report]
 per-client       task schema       tier policy    local | cheap     grading       client dashboard
 (email, CRM,     (one JSON task    per client     | frontier        (auto +       + monthly savings
 sheets, ticket   shape for all)    (this repo)    (this repo)       human QA)     report (calculator
 queue, ...)                                                         (this repo)   with ACTUALS)
```

- **Custom per client:** stage 1 (connector) and the QA policy in stage 5.
- **Identical for all:** stages 2–6 — which is this repo, hardened.
- The **calculator is the contract**: its inputs are the discovery checklist,
  its output is the proposal, and after month one its assumptions are replaced
  by the client's own ledger data — predicted-vs-actual becomes the renewal
  conversation.

## 4. Gap analysis: test bed → product

What exists is a single-tenant, single-box measurement system for one
developer's workload. The productization gaps, in the order they bite:

1. **Task-stream connectors + normalizer** (new): a tiny `Task` schema
   (id, text, context refs, tier hints) and per-source adapters. First two
   connectors should be chosen by the first pilot, not built speculatively.
2. **Queue + retry semantics** (new): the live run drove tasks synchronously;
   a product needs a durable queue, per-task state, and the retry policy the
   calculator already models (local fail -> cheap retry).
3. **Multi-tenant ledger** (small change): one ledger per client (a directory
   convention), same schema; the dashboard/report generator takes a client id.
4. **QA workflow** (new, small): the review slice the calculator prices needs
   a place to happen — even a generated "review these N outputs" HTML page is
   enough for pilots.
5. **Outcome grading beyond dev-speak** (adaptation): the outcome keyword
   lists are developer-reaction phrases; per-domain phrase packs (or pure
   model grading with the same precision guards) per task type.
6. **Deployment story** (ops): cloud-only mode needs nothing but API keys;
   local-box mode is llama-swap + models on client hardware — this repo's own
   deploy notes are the seed.

Explicitly **not** planned: a web SaaS, generic workflow builder UI, or
fine-tuning. The wedge is measured routing + honest reporting, not platform
breadth.

## 5. Sales motion (how the pieces are used)

1. **Discovery = calculator inputs.** Volume, minutes/task, hourly cost, task
   mix. 30 minutes with whoever owns the process.
2. **Proposal = calculator output.** Four worlds, net-of-fees savings,
   payback, sensitivity, fine print. The honesty block stays in.
3. **Pilot = one task stream, one month.** Cloud-only unless volume/privacy
   says box. Ledger on from day one.
4. **Renewal = predicted vs actual.** The monthly report re-runs the
   calculator with measured values. If we beat the prediction, that's the
   case study; if we missed, the client saw it before we did — either way the
   relationship survives contact with reality.

## 6. Honest risks

- **Cheap cloud keeps getting cheaper** — the local box's cost case erodes
  (the calculator already shows break-even ≈ 234k tasks/mo at today's list
  rates); the box increasingly sells on privacy/rate-limits, not dollars.
- **Small-N evidence**: today's measured numbers are one workload on one box.
  Every pilot fixes this a little; the report template must keep saying so.
- **Model quality drift**: rates and capabilities change monthly; reprice
  exists for cost, but capability re-evals per client task type are manual.
- **The 9B's blind spots are stable but real** — keyword-first routing is the
  mitigation, and per-domain keyword packs are per-engagement work.

## 7. Build order (next concrete steps)

1. Land the open PRs (fixes + live-run harness + calculator).
2. Accumulate real Claude Code/agent usage in the ledger (the owner's own
   machine) — richer defaults for the calculator within weeks.
3. `Task` schema + first connector, chosen by the first real pilot candidate.
4. Client-scoped ledger + monthly report generator (calculator + actuals).
5. QA review page generator.
```
