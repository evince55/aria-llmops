# Router escalation trigger — PRE-REGISTRATION

## Why this and not the tool adapter

The obvious next move after round 2 is to deploy the tool-call adapter. It cannot be deployed:
**nothing consumes tool calls.** `llmops.py`, `mlx_classifier.py`, telemetry and the dashboard
contain zero references to tool calling; the `LLMOPS_MLX_ADAPTER` seam serves round 1's *classifier*.
The adapter solves a four-tool surface I invented, on synthetic paths, graded against tasks I wrote.
Wiring it in would mean building a consumer for a model nothing needs — the inverse of round 1's own
first lesson about choosing by leverage rather than convenience. It was chosen for *measurement
headroom*, and that is exactly what makes it a bad deployment candidate.

**Rule A is the deployable result.** Agreement between two arms gave precision **1.00 on 32 of 32**
covered rows, and it is the only round-2 claim that never moved across every instrument change —
parser fix, wide set, operating points, template mode. This tests whether it transfers to the task
the router actually performs, using components that are already deployed and an instrument that is
**human-labeled rather than synthetic**.

## The question

Does agreement between the two deployed classifier arms — the keyword matcher and the tuned
`e2b_v3` — predict correctness on the 176-row human test set (3-lab unanimous labels)?

If it does, the router gets a principled escalation trigger: **agree → ship the local decision;
disagree → escalate.** That is cost control derived from evidence rather than intuition, which is
what this repo is for.

## Rules, fixed now

Rule A unchanged from `evals/ensemble.py`: identical labels → accept; different labels, or either arm
failing to answer → **abstain and escalate**. Reported as coverage, precision on covered, escalation
rate, and an end-to-end accuracy that counts an escalation as unanswered.

## The deployment gate — both criteria must hold

1. **`precision_on_covered` ≥ 0.90.** The covered set has to be genuinely safe, because those rows
   ship without escalation. The hybrid's overall accuracy is 0.761; a covered precision near that
   means agreement carries no information and the trigger is decoration.
2. **`coverage` ≥ 0.50.** Escalation costs money. If the trigger escalates most rows it is not a
   saving, it is a tax with extra steps.

Both, or it does not ship. **A trigger that is safe but escalates 80% of traffic fails, and so does
one that covers everything at the hybrid's own accuracy.**

## What would falsify it

- `precision_on_covered` ≈ 0.761 → the two arms agree on exactly the rows anyone would get right,
  and agreement is not a signal.
- Round 2 measured Rule A on a **synthetic** tool-call set. If it fails here, the finding was
  task-specific and the write-up must say the transfer was tested and did not hold.

## Stated in advance

The two arms are **not independent**: `classify_hybrid` already uses the 9B to rescue keyword
defaults, and the tuned E2B was trained on data that keyword-derived labels touched. Correlated arms
agree too often and inflate coverage while deflating the value of agreement. This is a real limit on
what a positive result licenses, and it is recorded before the number exists rather than after.
