# Converting an agent component to a small language model

### A reduced-scale reproduction of NVIDIA's SLM-agent conversion pipeline — and the fourteen things that broke

**Author's note on what this is.** This reproduces the *conversion methodology* from NVIDIA's
"Small Language Models are the Future of Agentic AI" (arXiv 2506.02153) — the S1–S6 pipeline for
replacing an LLM-backed agent component with a fine-tuned small model. It is a **reduced-scale**
reproduction on a **narrower task** than the paper's, and both divergences are stated precisely
below rather than buried. Consult the original for its own claims; what follows is what I built,
what it measured, and what went wrong.

The pipeline was run **twice**, on two different components. Round one converted a *classifier*: a
**3.2 GB fine-tuned model matched a 5.8 GB hybrid** on a live-measured promotion gate and now runs
as the default. Round one's own conclusion was that the target had been chosen badly, so round two
applied that lesson and converted an **agentic subtask** — the thing the paper is actually about —
where a **3.2 GB tuned model went from 0.55 to 1.00 and matched a purpose-built 9.5 GB tool-tuned
model at 34% of the memory**.

Those results occupy about a page. The other eleven pages are the failures, because they turned out
to be the more useful output.

---

## 1. The pipeline, and what each stage became here

The paper's argument is that most agentic subtasks are narrow, repetitive, and well within a small
model's reach — and that you can *convert* an existing LLM-backed component by mining your own
traffic. Its S1–S6 loop maps onto this repository as follows.

| Stage | Paper | This repo |
|---|---|---|
| **S1** collect | instrument the agent, log usage | `telemetry/schema.py`, `ingest_claude_code.py` — 5,692 usage events |
| **S2** curate | anonymise, filter | `harvest_eval_tasks.py` — structural scrubbing (paths, IPs, emails, tokens), model-side validity gate |
| **S3** cluster | find recurring operations | `evals/task_clusters.py` |
| **S4** select | choose candidate SLMs | `evals/capability_probe.py`, `routing_sol_eval.py` (oracle bound) |
| **S5** fine-tune | distil from a teacher, PEFT | `distill_generate.py` → `distill_to_mlx.py` → `mlx_lm lora` (QLoRA on 4-bit) |
| **S6** iterate | re-measure, redeploy | `promotion_gate.py` — a pre-registered promote/reject rule |

**The components converted.** Round one took the router's *task-difficulty classifier*: given a
developer task in prose, emit one of `SIMPLE / MODERATE / COMPLEX / CRITICAL`. The incumbent was a
keyword matcher with a served 9B model as fallback (5.8 GB). The challenger is a QLoRA-tuned
Gemma-4-E2B at 4-bit (3.2 GB), running in-process with no network dependency.

Round two took *tool-call emission*: given a task in prose and a four-tool schema, emit one correct
JSON call. This is an agentic subtask rather than a classification, and it needs **no teacher and no
judge** — ground truth is authored with the example, so verification is exact structural comparison.
That collapses S5's distillation step and makes the round free to run.

---

## 2. Fidelity — where this diverges from the paper

Stated first, because a reproduction that hides its gaps is advocacy.

| Dimension | Paper | This work | Consequence |
|---|---|---|---|
| Training examples | 10,000–100,000 | **677** (round 1), **460** (round 2) | ~7% and ~5% of the low end. Claims that depend on data scale are **not** tested here. |
| Task type | agentic subtasks (tool calls, multi-step work) | single-turn classification (round 1), **tool-call emission** (round 2) | Round 2 is on-target. Round 1 is not, and is labelled as such throughout. |
| Verification | — | LLM judge (round 1), **deterministic** (round 2) | Round 2 cannot be flattered by a judge; see findings 1 and 9 for why that matters. |
| Model family | several | one (Gemma-4 E2B/E4B), plus a 1-bit Bonsai-27B probe | Narrower selection stage than S4 intends. |
| Deployment | described | **shipped as default** | Stronger than the paper on this axis. |
| Adjudication | — | pre-registered gate, quarantined instruments, negative results published | Stronger than typical reproductions. |

**What this reproduction therefore does and does not license you to conclude.** It shows the
*pipeline* works end-to-end at small scale and produces a deployable model — twice, on two
different component types. Round two supports the paper's central claim on **one narrow agentic
subtask with a four-tool surface**; it says nothing about multi-step planning, long-horizon tool
use, or error recovery, and nothing about behaviour at the paper's data scale.

---

## 3. Results

### Round 1 — the classifier

Two eval instruments, both human-written, both quarantine-verified against training data in
both directions:

- **Test:** 176 rows from GitHub issues, 174 repos, 3-lab unanimous labels.
- **Dev:** 173 rows, same pipeline, disjoint queries — used for policy selection so the test set
  is spent once per question.

The gate rule was fixed **before** the first run: *promote iff accuracy ≥ incumbent AND no tier
recall regresses by more than 0.05.*

| Round | Config | Accuracy | Verdict |
|---|---|---|---|
| S5 | E2B tuned, 182 examples | 0.619 (42-row union) | feasibility only |
| S6 | E2B tuned, 677 examples | 0.744 | **REJECT** — COMPLEX −0.184 |
| S7 | E2B v3, COMPLEX slice regenerated | 0.761 | **PROMOTE** (provisional) |
| Live | same, incumbent *measured* not replayed | **0.761** | **PROMOTE**, 0 regressions |

**The headline.** `e2b_v3_rescue` ties the incumbent's accuracy at **55% of the memory** with **no
network dependency**. On CRITICAL — the tier where an error costs most — the 3.2 GB model matches
the 5.8 GB hybrid at **0.931**.

**The honest asterisk.** It is a *tie*, not a win, and the incumbent is slightly better on MODERATE
(0.617 vs 0.600). The case for shipping it is operational — half the memory, no endpoint — not
accuracy.

**The one causal chain worth the whole exercise.** S6's audit predicted a specific data defect: the
COMPLEX generator was emitting *prescribed-fix tickets* that named both cause and remedy, which read
as MODERATE. The prediction was recorded and not acted on. S7's gate then measured its cost at
**−18 points of COMPLEX recall** — the sole reason for rejection. Regenerating that slice with the
audit's fix (withhold the diagnosis) recovered **+21 points**, and the specific confusion the audit
named — COMPLEX misrouted to MODERATE — fell from **10 rows to 3**. Predicted, ignored, measured,
fixed, re-measured. That loop is the thing this pipeline is for.

### Round 2 — the agentic subtask

Round 1's own first lesson was *choose the converted component by leverage, not by convenience*, so
round 2 chose its target by measurement before building anything. Both candidates were probed on the
conversion target under strict programmatic verification:

| candidate | strict accuracy | verdict |
|---|---|---|
| code edit (file in → edited file out) | **1.00** | ceiling — nothing to learn |
| tool call (task + schema → JSON call) | **0.70** | 30 points of headroom |

The code-edit harness already existed and was the convenient choice. It was dead on arrival.

Two held-out sets: **standard** (n=20), and **adversarial** (n=13, using phrasings, extensions and
multi-word contents absent from *both* the training templates and the standard set).

| Arm | Interface | Size | Standard | Adversarial |
|---|---|---|---|---|
| Gemma-4-E2B base | prose | 3.2 GB | 0.55 | 0.69 |
| **E2B + tool adapter** | prose | **3.2 GB** | **1.00** | **0.85** |
| Ornith-1.0-9B (tool-tuned) | prose | 9.5 GB | 0.85 | 0.62 |
| **Ornith-1.0-9B** | **native `tools`** | 9.5 GB | **1.00** | **0.85** |

**Conversion ties selection, and wins on memory.** Fine-tuning was worth **+45 points** on the
standard set and **+16** on the adversarial one; the adversarial figure is the honest estimate,
since 1.00 on n=20 cannot be distinguished from 0.95. Against the selected model *measured through
its native interface* the result is a **dead tie on both sets** — so the claim is not that
conversion beats selection, it is that **a 3.2 GB QLoRA-tuned generalist matches a purpose-built
9.5 GB tool model at 34% of the memory, in-process, with no server**.

That correction came from this project's own caveat and is recorded in full as **finding 14**. It is
also the better result for the paper's thesis: the paper claims small models *suffice* for narrow
agentic subtasks, not that fine-tuning beats selection.

**The two arms fail on disjoint tasks.** Both score 11/13 on the adversarial set with **zero
overlap** in their errors — so they are complementary rather than redundant, and an ensemble would
outscore either. For a router that is directly actionable.

**What it learned is legible.** Parse rate went **0.92 → 1.00** while tool accuracy stayed at 0.92.
It did not learn *which* tool to call — the base already knew — it learned to emit the call correctly
and fill the arguments exactly. That is precisely the narrow, repetitive competence the paper claims
small models can absorb. It also generalised the one inference it was asked to: training says
*"terse" / "silently" / "keep it brief"*, the held-out set says *"quietly" / "no extra output"*.

**The asterisk that turned out to matter.** Ornith's prose-interface 0.62 was flagged on sight as
untrustworthy: its failures decomposed as 2 unparseable, 2 wrong tool, 1 wrong args, and a tool
accuracy of 0.69 for a *tool-tuned* model indicts the prompt, not the model. The re-test through the
native `tools` parameter moved it to **1.00 / 0.85** — the format was worth 15 and 23 points, and the
"conversion beats selection" headline evaporated with it. See finding 14.

---

## 4. The fourteen findings the paper does not contain

This is the part I would actually read.

**1. Agreement is not verification.** Two independent judge models agreed on all 495 labels — and
were wrong together. They graded *mechanism and diff size, not consequence*: one-line fixes for
DOM-XSS, a leaked bearer token, and an OAuth token in a URL fragment all came back SIMPLE. 73
downgrades against 5 upgrades. A shared blind spot passes through unanimity uncorrected. **Rotate
judge pairs, and audit the disagreement subset out of band.**

**2. Model-generated evals inflate model-trained models.** The same tuned model scored **0.92** on
a model-written eval set and **0.738** on a human-written one. Same quarantine, opposite verdict.
Not contamination — *distribution match*. If your eval set was written by a model, you are
measuring stylistic agreement.

**3. Natural usage cannot produce a balanced eval set.** Harvesting 5,426 real operator messages
yielded COMPLEX 2 / CRITICAL 1. An operator does not spend the day filing auth-bypass tickets;
rare-by-nature classes stay rare no matter how much traffic accrues. Tier-balanced instruments must
be built from a different source.

**4. Validation loss is not target accuracy.** The best-*val* checkpoint scored **0.667**; the final
checkpoint scored **0.738**. The val split was in-distribution synthetic; the eval was human prose.
**Select checkpoints on the target distribution, not the training one.**

**5. The degenerate-model artifact — encountered four times.** A model that emits one value for
every input looks like a working model with poor accuracy. It appeared as: a 1-bit model scoring a
false 0/4 (harness extraction bug), an 8-token generation cap capturing only a reasoning preamble
(exact always-MODERATE floor of 0.286), phantom outcome grades, and — worst — a *silent* keyword
fallback in production. **Assert that a classifier's outputs are not constant. This is now a
`degenerate_warning()` in the codebase.**

**6. Aggregate accuracy hides opposing tier movements.** At n=42, incumbent and challenger both
scored 0.810 — apparently a tie. At n=176 the same comparison was **+33 points MODERATE and −18
COMPLEX**. It was never a tie; it was a trade, and the small instrument could not see it. One
example moved accuracy 2.4 points at n=42.

**7. Sequential promotions drift past their own tolerance.** Each step passed a 0.05 per-tier rule
against its immediate predecessor: model swap −0.041, then a routing guard −0.021. Cumulatively
**−0.062** — a regression the gate had approved in two legal steps. A gate that only compares to the
*previous* config lets a chain walk arbitrarily far from the original. **Pin an original baseline
and check the cumulative delta.**

**8. "Output ratio" is meaningless under prompt caching.** The same events read **0.882** excluding
cached input and **0.003** including it — a 300× spread. Any token-efficiency metric must state its
denominator, and cached input tracks prompt reuse, not verbosity.

**9. A harness that lacks a capability the task needs turns an A/B into a confabulation contest.**
Grading file-edit tasks on a harness with no file access: the honest arm answered *"please provide
the file"* and was scored **wrong 31 times out of 31**; the other arm invented file contents and was
scored **right**. The result was a clean-looking **+0.245** improvement that measured willingness to
bluff. **The tell was the absolute rates** — 0.26 correctness on *simple* work is implausible.
Check plausibility before celebrating a delta.

**10. Test the cheap prediction before spending the expensive cycle.** Three mechanistic hypotheses
for one regression — a length confound (real in the data: the classes were 86% separable by length
alone), a register gap, a prior mismatch — were each tested in minutes and each refuted. Acting on
the most satisfying one would have cost a full generate → judge → tune → gate cycle and moved
nothing.

**11. Instrument the loop you actually spend on, not the one your architecture is about.** This
project's identity is *cheap routing*, so the cost model measured routing — which costs
approximately zero, because routing locally is the entire point. Meanwhile the **evaluation loop**
consumed a monthly subscription's rolling limit in a single day. `call_judge` shelled out to a CLI
and logged nothing. **A cost model that cannot see the line item that exhausts your budget is not a
cost model.**

**12. A serving flag can masquerade as a broken model for days.** Two different models, two quants,
producing coherent output for one to three tokens and then collapsing into topically-correct
word-salad. Diagnosed across two days as bad weights, bad quantisation, RoPE misconfiguration, chat
template mismatch, tokenizer mismatch — all refuted. The cause was `--repeat-penalty 0` on the
inference server: llama.cpp treats `1.0` as *no penalty*, and `0` is a degenerate divisor that
corrupts logits as the repeat window fills. **Check the serving configuration before the weights.**

**13. Quarantine on tasks is not quarantine on phrasings.** Round 2's first tuned arm scored a
perfect 1.00, and the quarantine check passed cleanly — no training task appeared in the held-out
set. But the training templates had been *written by mirroring the held-out wording*, so the model
could score by filling a slot in a sentence shape it had already seen. Exact-match quarantine cannot
detect this: the strings differ, the structure does not. The fix has to be structural — train-only
phrasings, plus a `phrasing_overlap()` guard that compares wording skeletons with the variable parts
masked and **refuses to generate a leaking set**. Two rounds of this project had already been bitten
by contaminated instruments; this was the third, and the first where the contamination was invisible
to the check that was supposed to catch it.

A second, smaller trap in the same round: `mlx_lm server` resolves models **by path on demand**, so
`--adapter-path` is silently ignored and a "tuned" run returns the base model's numbers. It was
caught only because the tuned arm reproduced the base's scores *exactly*. Direct probing on three
previously-failing cases settled it — base 0/3, tuned 3/3. **If two arms agree to three decimal
places, suspect your harness before your result.**

**14. A model measured through the wrong interface is not a baseline, it is a straw man.** Round 2's
selected arm, Ornith-1.0-9B, is fine-tuned for tool use and was measured by asking it for freeform
JSON in prose. It read 0.85 / 0.62 and made round 2's headline "conversion beats selection." Measured
through the OpenAI `tools` parameter it expects — same tool surface, same tasks, same grader, only
the delivery mechanism changed — it reads **1.00 / 0.85**, and the headline becomes a tie.

Nothing in the harness objected to the first number. It was flagged because the *failure
decomposition* was implausible for a tool-tuned model: 2 of 20 replies unparseable, tool accuracy
0.69. **A number inconsistent with what you already know about the system under test is a finding,
not a result** — and the check that catches it is reading the decomposition, not the score.

The correction cost one afternoon and turned a flattering claim into a defensible one. Two smaller
things fell out of it: `tool_choice: "auto"` and `"required"` score identically, so the gap was never
"declined to answer"; and the two arms fail on **disjoint** adversarial tasks, which means they are
complementary and an ensemble beats either.

---

## 5. What the gate rejecting things is worth

Four of this project's own proposals were killed by its own rules:

- **S6 challenger — REJECT.** Better accuracy, but COMPLEX regressed 0.184.
- **A brevity clause — KILL.** Big token savings, but correctness fell 0.083 past a pre-registered
  tolerance.
- **A routing guard — built, measured, then deleted.** It won +0.266 MODERATE against an incumbent
  that turned out to be a misconfigured server; against the fixed incumbent it won +0.066 for −0.062
  SIMPLE, and was removed.
- **An A/B round — VOIDED by its author.** See finding 9.
- **A conversion target — rejected before any work.** Round 2's code-edit candidate scored 1.00 on
  the base model. The harness for it already existed; it was dropped anyway.
- **A perfect score — discarded as contamination.** Round 2's first 1.00 was thrown out and re-earned
  on disjoint phrasings. See finding 13.
- **This project's own headline — overturned by this project.** "Conversion beats selection" held
  until the selected model was re-measured through the interface it was built for. It is a tie. See
  finding 14.

I list these because a gate that has never rejected anything is decoration. These are the evidence
that the adjudication was real.

---

## 6. What I would do differently

1. **Choose the converted component by leverage, not by convenience.** The classifier feeds a
   routing table that sends three of four tiers to the same model, so a *perfect* classifier changes
   only 16.5% of routing decisions — and those are the CRITICAL rows a keyword rule already catches
   at 0.931. The pipeline was sound; the target was low-leverage. **Round 2 acted on this**: it
   probed two candidates first and dropped the one whose base model already scored 1.00.
2. **Build the eval instrument first.** Two rounds were unresolvable because n=42 could not
   distinguish a tie from a trade, and a third was void because the harness could not perform the
   task population.
3. **Budget the evaluation, not just the inference.** See finding 11.
4. **Keep side-quests out.** A brevity-optimisation line ran three experiments and consumed most of
   the project's cash spend while advancing neither the reproduction nor the router.
5. **Prefer subtasks whose ground truth is constructible.** Round 2 cost roughly nothing to run
   because correct answers were authored with the examples — no teacher, no judge, no cloud spend.
   That is a property of the *task chosen*, not of the pipeline, and it is worth selecting for.

---

## 7. Reproducing this

```bash
pip install -r requirements-dev.txt          # mlx-lm, py3.10+; runtime itself is stdlib-only
python evals/distill_generate.py             # S5: teacher-labelled training set
python evals/judge_labels.py --in-file ...   # cross-model verification (rotate the pair)
python evals/distill_to_mlx.py               # → mlx-lm chat format
python -m mlx_lm lora --model <4bit-base> --train --data ... --iters 600
python evals/promotion_gate.py               # the pre-registered promote/reject decision
```

Round 2 is shorter, because it needs no teacher and no judge:

```bash
python evals/tool_call_data.py               # self-supervised, quarantine + phrasing guard
python -m mlx_lm lora --model <4bit-base> --train --data evals/datasets/distilled/tool_calls
python evals/tool_call_eval.py --adapter evals/adapters/e2b_tools_clean
python evals/tool_call_native.py --model <served-model> --set adversarial   # the selected arm
```

560 tests, CI on ubuntu/macos/windows × py3.9/3.13. Eval datasets and adapters are gitignored;
tooling and results are committed.

---

## 8. Status

**Converted, gated, deployed — twice.** The tuned 3.2 GB classifier is the router's default. The
tuned 3.2 GB tool-caller goes 0.55 → 1.00 on its own base and **matches a purpose-built 9.5 GB
tool-tuned model** measured through that model's native interface, at 34% of the memory and with no
server. The pipeline is re-runnable end to end.

The reproduction is honest about running at ~5–7% of the paper's data scale, and about round 1
converting a classifier rather than an agent. The fourteen findings above are, in my judgement,
worth more than either model.
