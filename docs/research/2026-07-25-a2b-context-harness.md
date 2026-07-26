# A2b, re-run on a valid harness — KEEP, by 0.2 of a task

**Date:** 2026-07-25 · **Follows:** `2026-07-25-a2b-invalid-round.md`
**Verdict: KEEP** (pre-registered rule). **Margin: 0.20 of one task.** Not a strong result.

The first A2b was void: the harness had no file access while the router's SIMPLE tier is
file-edit work, so the gate rewarded confabulation. This supplies the files.

## The harness

`evals/context_fixtures.py` — a 10-file synthetic repo and **60 tasks written against it**, each
rendered with its file inline, as a build agent would see it. 44 classify SIMPLE by the router
(the population a tier-conditional clause gates), giving a **2.2-task tolerance** vs A2's 1.2.

The guarantee is an invariant, not an intention: every task declares the exact tokens it targets,
and `unanswerable()` fails the run if any are missing from the named file. `local_traffic.py`
refuses to launch on drift. Without that, a fixture can wander from its task and the round is
void the same silent way, just harder to spot.

**Harness validity is visible in the numbers.** Absolute correctness is now 0.93–0.98, against
0.26/0.51 in the file-blind round. Simple edits, with the file present, should be nearly always
right — and now they are.

## Result (n=44, blind, position-randomised)

| measure | baseline | terse | Δ |
|---|---|---|---|
| correct | 0.955 | 0.932 | −0.023 |
| complete | 0.977 | 0.932 | **−0.045** |
| head-to-head | 3 | 3 | **38 ties** |

Position placement A 4 / B 2 / TIE 38 — no bias signal (and little to bias, at 6 decided).

**KEEP — and the margin is 0.20 of a single task.** The completeness delta is exactly 2 tasks
against a 2.2-task tolerance. One more task going the other way kills it. Both quality measures
trend negative and 38 of 44 are ties: on SIMPLE-tier work with context, the arms are close to
indistinguishable in quality.

## The one failure mode, and it is fixable

Both regressions are the same mechanism — terse returned **only the changed line** instead of the
edited file:

> **Task:** *"Add a comment marking the TODO with the tracking issue number ARIA-42."*
> **baseline (51 tok):** the whole file, TODO updated in place
> **terse (19 tok):** `# TODO(ARIA-42): add per-key expiry`

The *edit* is correct; a bare fragment is just ambiguous about where it goes, and both graders
called it incomplete. For an agent that applies patches, that is a real if minor usability
regression — and it has an obvious remedy: amend the clause to require the full edited file (or a
diff). The entire measured quality cost of brevity here is this one behaviour.

## The efficiency win is real, and half what the broken harness claimed

| | file-blind (void) | files supplied |
|---|---|---|
| output tokens | −80% | **−49%** (6,214 → 3,163) |
| wall clock | −78% | **−46%** (224.5 s → 120.5 s) |
| truncated at cap | 4 → 0 | 0 → 0 |

**Correction to the previous write-up**, which said the void round's efficiency numbers "stand".
They were counted correctly but measured the wrong scenario: the file-blind baseline burned
tokens *asking for files it could not see*, inflating the apparent saving. With context, it just
performs the edit. −49% is the honest figure.

## Recommendation

Do not ship on this alone. Two cheap moves, in order:

1. **Amend the clause** to require the full edited file, removing the only observed failure mode,
   and re-run. If it then passes with margin, the case is clean.
2. **Re-run larger.** A 0.20-of-a-task margin is not a decision, it is a coin flip with a verdict
   attached. n≈100 would make the tolerance ~5 tasks and settle it.

The pre-registered rule says KEEP and that is recorded honestly — but the finding this round
actually supports is narrower: *on SIMPLE-tier work with context supplied, brevity costs about
half the output tokens and roughly nothing in quality except a tendency to answer with fragments.*
