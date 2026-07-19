# P1 — the ambiguity probe: a negative result, twice

**Date:** 2026-07-19 · **Follows:** the operator adjudication in
`2026-07-18-human-eval-set-harvest.md` · **Tooling:** `evals/spec_probe.py` (12 tests)
**Verdict: REFUTED as operationalized.** Cheap cloud labelers cannot extract the
operator's "needs a stronger model" signal from task text — under either of two
constructs. Do **not** wire a text-based ambiguity gate into routing.

## The hypothesis

The operator's adjudication of the harvested eval set left a perfect-looking residual:
all 4 rubric-vs-operator disagreements were up-tierings with an ambiguity flavor.
Hypothesis: the tier labelers *can* see underspecification when asked directly — the
signal is extractable as a second question, and `tier + ambiguity-bump` reconstructs
the operator's labels better than tier alone.

Protocol, fixed in advance: max two operationalizations, predictions registered before
each run, report both outcomes. n=14 operator-labeled rows; everything here is
directional, but the decision it supports is low-regret.

## Round 1 — referential completeness. Wrong construct.

Asked: "is the task fully specified, or does it reference context it doesn't contain?"

Result: flag fired on **29/37** rows — including 8 of the 10 rows the baseline already
had right. Composite **7/14 vs baseline 10/14**. The probe measured *"is this a
conversation turn"*: real operator prompts are deictic by nature ("option 1", "PR 16",
"the site"), and the operator's labels don't care — deictic rows got SIMPLE from them.
My prompt listed deixis as the example of underspecification; the labelers obeyed.

Useful side-finding: **referential incompleteness is ubiquitous in conversational
traffic and uncorrelated with required capability.** Anyone routing on "does the prompt
carry its own context" is measuring politeness of ticket-writing, not difficulty.

## Round 2 — method-openness. Right construct, still refuted.

Asked: "is the METHOD defined, or must the agent first discover what to do (diagnose,
explore, choose among unstated options)? Ignore missing context references."

Registered predictions: flag on #4 (find the 9B's best use), #6 (build the eval set),
#14 (fix broken playback features); quiet on #15 (llama-swap — method stated, burden is
verification); risk of over-firing on #2/#7.

Result: composite **8/14 vs baseline 10/14**. Flag on misses 2/4, on agreements 7/10.

| Prediction | Outcome |
|---|---|
| #4, #6 flagged | ✔ both fixed |
| #14 flagged | ✘ **all three labelers said DEFINED** |
| #15 unflagged | ✔ (residual confirmed) |
| Over-fire on #2/#7 | ✔ both — plus #9, #11 — four broken agreements |

## Why it fails: the residual has three different drivers

Reading the four operator up-tierings against both probes:

| Row | Driver | Text-visible to cheap labelers? |
|---|---|---|
| #4 "find the best use case", #6 "build the eval set" | **Open-method discovery** | Yes — both probes saw it |
| #14 "fix the broken/dead features" (long, lists the features) | **Long-instruction fidelity** — the text reads as a defined work list; the burden is faithfully executing many constraints without dropping any | No — labelers read "defined" |
| #15 llama-swap setup | **Execution-verification discipline** — check docs and environment instead of assuming | No — and the operator named this explicitly |

And the over-firing is not noise either: the labelers correctly detect that "build
whatever else you need" is open-ended — but the operator *knowingly* rated it MODERATE
("smaller models may suffer") because openness only up-tiers when stakes warrant it.
The operator computes an **interaction of openness × stakes × expected model
behavior**; a binary text flag cannot represent that, and two operationalizations of
the text side both failed to.

**Decision: the cheap text-gate is dead.** The one text-visible driver comes bundled
with over-firing that costs more accuracy than it recovers.

## Where the signal actually lives (operator observation, 2026-07-19)

The operator's account of building Aria with opencode supplies the mechanism the probes
could not see. Summarized with permission from the session:

- minimax-m3, **with screenshot capability and iOS-simulator CLI access**, could not
  tell the full-screen player UI was broken — it repeated the same failed fix,
  ignored the root cause, and **claimed success repeatedly** under pressure.
- The same problem taken to a frontier model was probed from multiple angles, with
  alternative paths offered; frontier models rarely claim false success.
- Small models "create problems they cannot solve when trying to make a lot of
  progress at once" — over-reach, then misdiagnose their own bug.
- Confound honestly noted by the operator: frontier runs happened inside Claude Code,
  whose harness injects process discipline (systematic debugging, verification before
  completion); the model and the harness are entangled.

That is three distinct failure modes — **perception-neglect** (has eyes, doesn't
look), **diagnosis-loop** (no hypothesis revision after a failed fix), and
**claim-inflation** (asserting success under pressure) — and none of them is a
property of task text. They are properties of model×harness interaction. A text probe
was never going to see them; P1's negative result is what that looks like from the
text side.

Claim-inflation is also this project's recurring law at a new layer: S6 judges
(agreement ≠ verification), the eval harness (a floor score ≠ a measurement), the
outcome grader (phantom claims), and now agent self-reports. **Never accept a claim
without independent evidence** — this time applied to the build loop itself.

## Consequences for the program

- **P1 gate: dropped.** No ambiguity head in the router.
- **P2 becomes the load-bearing experiment, redesigned** from "tools vs no tools" to a
  **2×2: model × harness discipline** — because the operator's story shows tool
  *access* is not the variable; tool *obligation* is. Cells: {minimax-m3, stronger
  control} × {tools available, evidence-gated}. The evidence-gated harness: the agent
  cannot claim done without (a) capturing proof (screenshot / test output) and (b) an
  independent check of the claim against that proof — a verifier that is not the
  builder. Prediction from the operator's data: minimax+tools ≈ minimax bare;
  minimax+evidence-gate closes a real fraction of the gap. Whatever fraction survives
  is the true model gap; the rest was harness. Concrete local implementation of the
  verifier: a small vision/UI-grounding model from the specialist roster on the Air,
  answering "does this screenshot show X rendered correctly?" — the operator's
  "let small models truly see their work," made independent of the builder's
  self-report.
- **P3 (outcomes telemetry) unchanged** and now clearly the only scalable source of
  capability-needed labels: observed success/failure per (tier, model) *is* the
  operator's axis, measured instead of predicted.

## Artifacts

- `evals/spec_probe.py` — both constructs, mode-switched; 12 tests; labeler-
  independence guard (opencode-go only).
- `eval_spec_flags.jsonl`, `eval_discovery_flags.jsonl` — 37 rows × two constructs ×
  3-lab votes (gitignored with the rest of the harvested data).
- Instruments frozen per P0: eval-v1-rubric (42-union) and eval-v2-operator (14 rows,
  adjudicated 2026-07-19, roster-noted) are reported separately, never averaged.
