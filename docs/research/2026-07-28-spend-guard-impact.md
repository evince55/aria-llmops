# The circuit breaker — and what it changes about what this project is

**Date:** 2026-07-28 · Prompted by Google/Kaggle, *"The New SDLC With Vibe Coding"* (June 2026),
recommendation #14: *"Add per-task token/cost budget caps and circuit breakers — the published
Anthropic architectures have none, and it's the gap that bites at scale."*

## The design changed once I looked at the actual incident

"Per-task cost cap" is the obvious reading and it is the wrong one **here**. The event this exists to
prevent — the A2/A2b grading runs consuming a monthly subscription's rolling limit in a single day —
was not one expensive task. It was **hundreds of cheap judge calls in a loop**. A per-call cap would
have passed every single one of them.

So the budget is scoped to a **run**, charged **before** each call, with an optional **call cap**
alongside the dollar cap, because a loop that never reaches a dollar limit and never terminates is
the same disaster arriving more slowly.

It is also wired into the loop the money actually goes to. Finding 11 said *instrument the loop you
actually spend on, not the one your architecture is about*; the same applies to enforcement. A
breaker on the router would be architecturally tidy and operationally useless — routed inference runs
on **free local models**, while `judge_event` is called from exactly one place, `evals/judge_labels.py`,
and that is where the money went.

## Three properties, each earned by a finding in this repo

- **No default budget.** Findings 12, 19 and 23 were every one of them an unnamed default — a serving
  flag, a sampling temperature, a chat-template mode — that nobody chose and nothing recorded. A
  guard with a default budget is a guard whose limit nobody decided, so `budget_usd` raises when
  omitted.
- **Fails closed.** An unpriceable call raises rather than counting as `$0`. `cost_control` already
  flags unpriced models instead of zeroing them; treating "I don't know what this cost" as free is
  exactly how an unmetered loop comes to look like a free one.
- **Stays tripped.** Once the budget is gone it refuses everything, not just what it cannot afford. A
  run that limps on in small increments *is* the original incident.

Partial results survive the trip. The spend already happened; discarding what it bought would make
the guard cost money instead of saving it.

---

## What this changes about the final product

### 1. It moves the repo from monitoring to governance

Every other component here **measures**. This one **refuses**. For a portfolio piece about production
LLMOps that is not a small difference: "I measured cost per task" is analysis, and "the system
enforces a budget and stops itself" is engineering. The Google paper's own framing is that published
agent architectures *lack* this, so it reads as a gap closed rather than a box ticked.

It also completes a sentence the write-up already started. Finding 11 says *a cost model that cannot
see the line item exhausting your budget is not a cost model.* The natural completion, and now
finding 25: **a cost model that can see the spend but cannot stop it is not cost control.**

### 2. It is the first component whose value cannot be evaluated

Everything in round 2 carries a number on a held-out set. This carries none, and cannot. Its output
is **disasters that did not happen**, which is unmeasurable by construction.

That is worth stating plainly rather than papering over, because this project's whole posture is that
claims need instruments. The honest position: the guard is **testable but not evaluable**. Sixteen
tests fix its behaviour — stops before spending rather than after, refuses an unpriceable call,
stays tripped, keeps partial results, and is actually wired into the loop that spends. What no test
can tell you is whether the limit is set correctly, and no eval will ever tell you that either.

### 3. The main risk is the failure mode this project keeps finding

A guard that is silently misconfigured is worse than no guard, because it is *believed*. That is
findings 12, 19 and 23 in a new costume — and a breaker is a particularly bad place for it, since
nothing looks different until the day it should have fired and didn't.

Mitigations are structural rather than advisory: no default budget (it raises), a loud `LOG.error` on
trip carrying the guard's name, the reason preserved in `report()`, and failure closed on unpriceable
calls. The one thing not defended against is a budget set too *high* — that fails silently and by
design, and it is the residual risk.

### 4. It is opt-in, which is a real tradeoff

`--budget-usd` defaults to `None`, so existing runs are unchanged and an unguarded run stays possible.
That is deliberate: a mandatory budget on every entry point would be a guess baked into a default,
which is the thing finding 19 is about. The cost is that **the protection only applies when someone
remembers to ask for it** — and the person who forgot last time was me.

The honest resolution is a habit, not a flag: any batch that calls a paid model gets a budget, and
the CLI help says what happens without one.

---

## What I am not claiming

That this would have prevented the incident. It would have **bounded** it — the run would have
stopped at whatever ceiling was set — but only if a ceiling had been set, and on 2026-07-25 nobody
was thinking about ceilings. Tooling makes the right thing possible; it does not make it automatic.
The write-up should say that rather than implying a solved problem.
