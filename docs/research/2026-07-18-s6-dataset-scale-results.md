# S6 — scaling and balancing the router training set

**Date:** 2026-07-18 · **Follows:** `2026-07-17-s5-thin-slice-results.md`
**Status:** dataset complete and quarantined; fine-tune not yet run.

S5 proved the flywheel closes (E2B base 0.286 → tuned 0.619) and quantified exactly why it
fell short of the 9B's 0.762: **class imbalance × tiny n**. COMPLEX had 13 training examples
and scored 0.23 recall. S6 is the fix — more data, balanced per tier.

## What ran

1. **Generate** (16 agents, 4 tiers × 4 domains) — 640 original tasks, exactly 160/tier and
   160/domain, 640 unique strings. Domains: iOS/Swift, Python/FastAPI, web frontend, infra/CI.
2. **Judge** (`evals/judge_labels.py`) — every task independently labeled by **two
   opencode-go models of different families**, `minimax-m3` and `deepseek-v4-pro`. Keep only
   where both agree. 495/640 survived (77.3%); 0 rows failed to label.
3. **Audit** — an independent per-tier pass that tries to *refute* the surviving labels.
4. **Assemble** (`evals/apply_label_policy.py`) — policy, dedup, quarantine, balance, merge.

## The finding: agreement is not verification

The audit found a reproducible failure in the two-judge scheme, and it is the most useful
result of this increment:

> **The agreed label is reliable when the judges CONFIRM the generator's intent, and
> unreliable when they OVERRIDE it.**

Overrides are almost entirely one-directional — measured on all 495 judged rows:

| Direction | Count |
|---|---|
| Downgrades (judges moved the task DOWN the rubric) | **73** |
| Upgrades | **5** |

Root cause, named independently by two auditors: **the judges grade mechanism and diff size,
not consequence.** The rubric says to escalate on consequence; the judges escalate on effort.
Concretely, all five of these were labeled **SIMPLE** because the patch is one line:

- `location.hash` read into `innerHTML` — DOM-based XSS
- `$('#results').html(response)` with raw search JSON — stored XSS
- third-party CDN `<script src>` with no SRI in an authenticated app
- OAuth access token left in the URL fragment
- axios interceptor leaking the bearer token to third-party analytics/CDN origins

A third auditor identified the methodological cause: **the judge pair is identical on all 495
rows**, so "survived cross-model judging" provides no independent check — a *shared* blind spot
passes through uncorrected. Agreement measures consistency, not correctness. Only the
out-of-band audit could see it.

**Why this decides the policy.** For a router the error is asymmetric. Over-escalation wastes
money on a stronger model. Under-escalation routes XSS, credential-leak, and destructive
migration work to the cheapest local tier — the most expensive mistake this classifier can
make. Left uncorrected, this dataset would have taught exactly that.

## The policy

Implemented in `evals/apply_label_policy.py` (11 tests), applied to all 495 judged rows:

| Case | Action | Rows |
|---|---|---|
| Judges confirm intent | keep agreed label | 417 |
| Judges **upgrade** intent | keep agreed label (audit confirmed all 5 correct) | 5 |
| Judges **downgrade** intent | **revert to intent** | 72 |
| Downgrade the audit affirmed | keep agreed label (carve-out) | 1 |

The single carve-out is the `PlayerManager` queue data race (intent CRITICAL, agreed COMPLEX):
both the COMPLEX and CRITICAL auditors agreed COMPLEX is right — a recoverable crash with no
persisted corruption, no data exposure, and no money involved. It lives in a documented
`CARVE_OUTS` list rather than as a hand-edit, so the rule stays reviewable and reproducible.

Two auditors disagreed on ~2 boundary rows (the `AuthManager` token-refresh race and the CSP
tightening). That is genuine COMPLEX/CRITICAL ambiguity, not a defect; both resolve to
CRITICAL under the blanket rule and the disagreement is recorded here rather than smoothed over.

## Result — balance

`evals/datasets/distilled/train_v2.jsonl`, **677 examples**, all unique:

| Tier | S5 | S6 | Change |
|---|---|---|---|
| SIMPLE | 34 | 194 | 5.7× |
| MODERATE | 122 | 245 | 2.0× |
| **COMPLEX** | **13** | **119** | **9.2×** |
| **CRITICAL** | **13** | **119** | **9.2×** |
| **Total** | **182** | **677** | 3.7× |

Imbalance ratio (largest:smallest tier) improves **9.4:1 → 2.06:1**. Every tier clears the
100-example floor. The two tiers that collapsed in S5 are no longer starved.

**Quarantine verified independently** (not just asserted by the assembling agent): 0 exact
and 0 fuzzy (≥0.90 ratio) overlaps against all 129 held-out eval tasks across
`labeled_tasks{,_prose,_balanced}.jsonl`. The quarantine set was also positively confirmed
non-empty — an empty set would make the filter a silent no-op.

An ablation baseline preserving the raw judge-agreed labels is kept at
`train_v2_agreed.jsonl` (COMPLEX 85, imbalance 3.16:1, COMPLEX 15 below floor). Training both
would measure whether *label policy* or *data volume* drives the gain — a worthwhile experiment
if the GPU time is available.

## What to change before the next scale run

The audits produced specific, actionable upstream fixes. These are worth more than the data:

**Generator prompts**
- *COMPLEX* — the generator emitted "prescribed-fix tickets": tasks naming both the cause and
  the remedy ("N+1 → switch to `selectinload`"), which read as MODERATE wiring dressed in
  concurrency vocabulary. Only 66/160 held COMPLEX. **Withhold the diagnosis and the fix**;
  tasks that preserve genuine uncertainty ("I suspect… root-cause it") hold COMPLEX reliably.
  Budget ~2.4× over-generation for this tier.
- *MODERATE* — single-artifact tasks (one manifest, one component) read as SIMPLE regardless of
  feature framing. Steer toward explicit **cross-file wiring**.
- *SIMPLE* — prod-manifest selector/label edits must not be generated as SIMPLE. A
  `commonLabels` change to a live prod overlay repoints an immutable `spec.selector` and can
  take the service to zero endpoints; that was born mislabeled by the generator.

**Judge prompt**
- Add an explicit guard: **diff size never caps tier**, and a security/auth/data-exposure hole
  is CRITICAL even when the fix is one line.

**Judge diversity**
- Rotate the judge pair per shard instead of using one fixed pair, so a shared blind spot
  cannot pass uniformly through the whole dataset.

**Coverage gap** — SIMPLE agreed 160/160 at 100%, verified as real signal by a planted-task
control (both judges scored 10/10 separating real SIMPLE tasks from injected CRITICAL/COMPLEX
ones). But tasks that unambiguous teach little about the SIMPLE/MODERATE boundary, which is
where the real disagreement lives. Future generation should target boundaries, not centroids.

## Next

Re-tune E2B on `train_v2.jsonl` (LR 1e-4, the rate E2B converged at in S5) and re-run the eval
gate against the 9B's 0.762. Promote — swap `CLASSIFIER_MODEL` — only if the tuned SLM matches
or beats the 9B on the quarantined union **with no tier regression**. E4B stays out unless it
gets the gentler recipe (grad clip + LR schedule); it never converged in S5.
