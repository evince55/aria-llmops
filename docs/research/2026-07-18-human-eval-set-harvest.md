# Harvesting a human-written eval set — what it fixed, and what it can't

**Date:** 2026-07-18 · **Follows:** `2026-07-18-s6-finetune-gate-results.md`
**Status:** tooling complete, 23 rows provisionally labeled, 15 awaiting human adjudication.
**Headline:** real usage cannot produce a tier-balanced eval set, and that is a property of
usage, not of the harvester.

## Why

The S6 gate stalled on eval resolution, not on the model: at n=42, one example is 2.4 points,
so `0.810` vs `0.810` was unresolvable. It also showed that a *model-generated* eval set
inflates a model trained on model text — E2B-v2 scored 0.92 on `labeled_tasks_balanced.jsonl`
(`source: 35b-gen`) versus 0.738 on the human union. Not contamination; distribution match.

So the eval set had to grow with **human-written** text. We cannot synthesize that, so we
harvest it: the operator's own prompts, already on disk.

## Pipeline

`evals/harvest_eval_tasks.py` → `evals/label_eval_set.py`

1. **Harvest** 5,426 messages (24 session transcripts + route-decision telemetry).
2. **Filter** to task-shaped prompts, and **scrub** before writing.
3. **Validity gate** — three models vote on "is this a self-contained engineering task?"
4. **Label** survivors with three models; unanimous → provisional, contested → human queue.

### Yield

| Stage | Count |
|---|---|
| Raw messages | 5,426 |
| Task-shaped after filtering | 70 |
| Survived the validity gate | 38 (**32 rejected as non-tasks**) |
| Unanimous 3-model label | 23 |
| Contested → human review | 15 |

## Two design rules this had to obey

**Labeler independence.** A grader may not be a component of the system under test. Labeling
with the keyword classifier would make the hybrid router score against its own output; the same
holds for the 9B or a tuned E2B. `label_eval_set.py` refuses any labeler that is not an
opencode-go cloud model, and uses **three different labs** (minimax-m3, deepseek-v4-pro,
glm-5.2) after S6 showed a fixed pair shares blind spots. The S6 audit's prompt correction
("diff size never caps tier") is applied.

**Disagreement is kept, not dropped.** The opposite of the training-data rule: a contested
training label is noise, but a contested *eval* row is a genuine boundary case and the most
valuable thing a human can adjudicate. The queue is sorted widest-disagreement-first.

## The finding: natural usage is MODERATE-heavy

Provisional labels: **MODERATE 13 · SIMPLE 7 · COMPLEX 2 · CRITICAL 1.**

Merged with the existing 42-row union, CRITICAL moves from 7 to ~8. **The tier where a routing
error is most expensive remains the thinnest, and harvesting more usage will not fix it** — an
operator does not spend their day typing auth-bypass tickets. Rare-by-nature classes are rare in
harvested data by construction.

This splits the eval problem in two, and the project needs both instruments:

| Instrument | Question it answers | How to build it |
|---|---|---|
| **Natural-distribution** (this harvest) | "How accurate is the router on my actual traffic?" | Harvest — done |
| **Tier-balanced** | "What is per-tier recall, especially CRITICAL?" | Cannot be harvested |

The natural-distribution set is arguably the more honest production number and we did not have
one before. It just cannot answer the CRITICAL question.

**For the balanced set, do not generate CRITICAL examples with a model** — that reintroduces the
0.92 style-match problem. The honest source is human-written security and incident text that
already exists: CVE write-ups, security advisories, public incident postmortems, and GitHub
issues. Different humans than the operator, but still human, and genuinely CRITICAL.

## The first harvest was polluted (and why it matters)

The initial run returned 86 rows including "Proceed with merge, it works fine." and "It looks
good, go ahead and merge it." — the action-verb filter treated `merge`, `make`, and `set up` as
task signals, so approvals and agent-behaviour instructions got through. Grading a router on
those measures nothing.

Fixed with `_APPROVAL_LEAD` / `_META_INSTRUCTION` for the deterministic cases plus the
model-side validity gate for the rest; the gate then rejected 46% of what regex still let
through, which is the honest rate for conversational transcript text. A test also caught that
`deploy`, `restart`, and `upgrade` were missing from the verb list entirely.

## Privacy

These are real operator prompts and this repo is public.

- Structural scrubbing (home paths, IPs, emails, SSH keys, token-shaped strings) verifies clean:
  **0 hits** across all four patterns.
- Literal terms (usernames, hostnames, handles) redact via `--redact` at call time. This is a
  parameter, not a constant: writing them into this file would leak exactly what the function
  removes.
- **Limitation:** literal redaction misses typo variants — e.g. a `examplehost.co` misspelling
  survives an `examplehost.com` rule. Human review before publication is required, not optional.
- The harvested `.jsonl` files are **gitignored**. Tooling is committed; the data is not, unless
  the owner reviews and opts in.

## Next

1. **Adjudicate the 15-row review queue** (`eval_review_queue.jsonl`, `expected_tier: null`).
   These are boundary cases; model consensus already failed on them, which is exactly why a
   human is the right instrument.
2. Optionally spot-check the 23 provisional rows — they are 3-model consensus, not human-verified.
3. **Build the balanced CRITICAL/COMPLEX set from public human-written incident text**, per above.
4. Re-run the S6 gate against both instruments and report them separately. Do not average them:
   they answer different questions.

---

## Tier-balanced set built (2026-07-20): 176 human-written rows from GitHub issues

The natural-distribution harvest above could not produce a tier-balanced instrument
(COMPLEX 2 / CRITICAL 1) — that is a property of operator usage, not of the harvester.
This is the second instrument it called for, built from the source that section
recommended: public human-written issue text.

**Result: 176 rows, 174 distinct repos, 3-lab unanimous labels, quarantine-clean.**

| Tier | Operator harvest | GitHub set |
|---|---|---|
| SIMPLE | 7 | 49 |
| MODERATE | 13 | 60 |
| COMPLEX | 2 | **38** |
| CRITICAL | 1 | **29** |

Imbalance ratio 2.07; smallest tier 29. Statistical power vs the 42-row union: one
example moves accuracy 0.57 points instead of 2.4 — roughly 4× the resolution, which is
what the 0.810 tie needs to be resolvable at all.

**Method.** `evals/harvest_github_tasks.py` → the existing `label_eval_set.py` unchanged
(3 independent opencode-go labs + validity gate + agreement split). The load-bearing rule:
**the search query is a sampling strategy, never a label** — querying "XSS" only
oversamples that subject matter so rare tiers exist; the tier comes solely from the
labelers, none of which is a component of the router under test.

**Quarantine verified in both directions:** 0 exact and 0 fuzzy (≥0.90) overlap against
`train_v2.jsonl` (677 rows), and 0 overlap with the existing 129-row eval union — so the
two instruments stay independent and can be reported separately, never averaged.

**Defects found by inspecting data rather than counts** (the recurring lesson): non-English
rows passed an ASCII-only filter (accented French is ~95% ASCII, so English function words
are required too); a 9-character task passed a 40-char minimum because the pre-filter
measured title+body while the emitted task is title+first-sentence; and the critical bucket
was initially *disclosure notices* ("Hello, I'm a security engineer at…") rather than work,
which starved the scarcest tier until queries were retargeted at the vulnerability **class**
(XSS/CSRF/RCE/traversal/privilege-escalation), taking critical-ish candidates 31 → 83.

A second targeted pass was needed for COMPLEX: S6's audit found that tickets naming both
cause and fix collapse to MODERATE, so the boost queried *unresolved diagnosis* language
("intermittent", "cannot reproduce", "root cause", "N+1", "goroutine leak") — +22 COMPLEX.
Its unanimity was only 37% vs 62.5% for the first pass, which is the expected signature of
deliberately sampling boundary cases.

**139 contested rows are preserved** (`gh_review_queue*.jsonl`), not dropped — a contested
eval row is a boundary case worth human adjudication. 11 of them are three-way splits (all
three labs disagreed), which is the high-value slice; the rest are single-boundary. The 176
unanimous rows stand as a usable instrument without any adjudication.

**Next:** re-run the eval gate on this set to resolve promote/reject for the tuned E2B —
the missing half of the S1–S6 reproduction.
