# Converting an agent component to a small language model

### A reduced-scale reproduction of NVIDIA's SLM-agent conversion pipeline — and the twenty-four things that broke

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
where on a 61-task held-out set a **3.2 GB tuned model beats a purpose-built 9.5 GB tool-tuned model
0.82 to 0.62 at 34% of the memory**, at every interface and operating point tested.

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
| Training examples | 10,000–100,000 | **677** (round 1), **460→10,000** (round 2) | Round 2 now spans the paper's low end. At fixed compute the curve **plateaus**: 21× the data buys 3.3 points and regresses past 2,500. |
| Task type | agentic subtasks (tool calls, multi-step work) | single-turn classification (round 1), **tool-call emission** (round 2) | Round 2 is on-target. Round 1 is not, and is labelled as such throughout. |
| Verification | — | LLM judge (round 1), **deterministic** (round 2) | Round 2 cannot be flattered by a judge; see findings 1 and 9 for why that matters. |
| Model family | several | **two converted** (Gemma-4-E2B, Qwen3.5-9B), plus a 1-bit Bonsai-27B probe | Both converge to **exactly 0.885** from bases 21 points apart — the result is the pipeline's, not one checkpoint's. |
| Deployment | described | **shipped as default** | Stronger than the paper on this axis. |
| Adjudication | — | pre-registered gate, quarantined instruments, negative results published | Stronger than typical reproductions. |

**What this reproduction therefore does and does not license you to conclude.** It shows the
*pipeline* works end-to-end and produces a deployable model — twice, on two different component
types — and that on this subtask the pipeline's output is **insensitive to training-set size across
a 21× range** at a fixed training budget. Round two supports the paper's central claim on **one narrow
agentic subtask with a four-tool surface**; it says nothing about multi-step planning, long-horizon
tool use, or error recovery. On data scale it now says something specific and limited: within
460–10,000 examples of *template-generated* data at a fixed budget, more data does not help.

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

Three held-out sets. **standard** (n=20) and **adversarial** (n=13) were the original instruments;
both turned out to be too small and too easy, so the reported instrument is now **wide** (n=61) — the
original 13 plus a 48-row FRESH slice authored against structural axes fixed in a pre-registration
before any task was written. A separate REGRESSION slice targets known failure modes and is excluded
from every generalisation number, because it is fitted to them by construction.

Every arm is reported at **both** operating points — greedy, and the sampling config the checkpoint
itself ships — because neither is automatically "correct" (finding 19) and the choice was previously
unnamed in both directions.

| Arm | Interface | Size | wide @ greedy | wide @ card | spread @ card |
|---|---|---|---|---|---|
| Gemma-4-E2B base | prose | 3.2 GB | 0.541 | 0.530 | 0.115 |
| **E2B + tool adapter** | prose | **3.2 GB** | **0.820** | **0.820** | **0.066** |
| Ornith-1.0-9B | native `tools` | 9.5 GB | 0.623 | 0.563 | 0.049 |
| Ornith-1.0-9B | prose | 9.5 GB | 0.574 | 0.508 | 0.164 |

E2B's card point is `temp 1.0 / top_p 0.95 / top_k 64`; Ornith's is `temp 1.0`. Card-point figures
are the mean of 3 runs — above temperature 0 a single run is a sample, not a score.

On the small sets both the tuned E2B and Ornith-native scored **1.00 (n=20)** and **0.846 (n=13)** —
identical, twice — and this document previously reported that as a tie. It was a **ceiling**; see
finding 18. On n=61 they separate by 0.197.

These are the **corrected** numbers. The first published version of this table understated both
prose baselines, because the harness's own output parser was mis-scoring any model that reasons
before answering — see finding 15. The `± .05` on the prose row is measured run-to-run spread, not
an estimate; the served prose interface is nondeterministic at `temperature: 0` while the native
`tools` interface is not.

**Conversion beats selection at every combination of interface and operating point.** At Ornith's
best — native interface, greedy — the 9.5 GB tool-tuned model reaches 0.623 against the tuned 3.2 GB
model's **0.820**; at its card point, 0.563 against the same 0.820. At 34% of the memory, in-process,
with no server.

Fine-tuning's gain on the fresh instrument is **+33.3 points** (0.479 → 0.812). That figure has now
been published three times, each on a better instrument: +15.6 with a broken parser, +7.7 after the
parser fix on n=13, and +33.3 on n=48. The n=13 base score was noise — the same arm collapses 29
points on fresh tasks — so it is **withdrawn rather than averaged**.

**The gain lands where the training aimed, not everywhere.** On the fresh slice `run_tests` goes
**5/12 → 12/12** — verbosity inferred from idioms never seen in training (*"full firehose"*, *"chatty
mode"*, *"hush"*) — while `read_file`, where the base was already at 11/12, shows **no gain and a
small cost**. A uniform lift would suggest the eval was measuring something generic; this is
localised to what was trained, which is the paper's thesis behaving as advertised.

**The second family confirms it is the pipeline, not the checkpoint.** Converting `Qwen3.5-9B` on the
identical subtask, data and schedule gains **+0.131** — above the pre-registered 0.10 floor — and both
converted families land on **exactly 0.885 (54/61)** from bases 21 points apart (0.541 vs 0.754).
Conversion erases the gap between them entirely.

| Arm | Size | strict | tool | out tokens |
|---|---|---|---|---|
| Gemma-4-E2B base | 3.2 GB | 0.541 | 0.918 | — |
| **E2B + tool adapter** | 3.2 GB | **0.885** | 0.918 | 24 |
| Qwen3.5-9B base | 5.6 GB | 0.754 | 0.951 | 35 |
| **Qwen3.5-9B + tool adapter** | 5.6 GB | **0.885** | 0.984 | 23 |

**The two arms fail on disjoint tasks, and the ensemble was pre-registered before it was run.** Both
score 11/13 on the adversarial set with **zero overlap** in their errors, and Ornith-native fails the
*same two rows* on every run. An agree-or-escalate cascade over the two therefore covers **9/13 rows
at precision 1.00**, escalating exactly the 4 rows where one of them is wrong.

Its end-to-end accuracy is **0.692 — worse than either arm alone**, which is what the pre-registration
predicted: two arms with no tiebreaker cannot vote, so a cascade cannot raise accuracy. What it buys
is a **trust signal**, and for a router that is the useful part — 69% of tool calls served by a 3.2 GB
model with zero errors, 31% escalated.

On the wide set the cascade holds: **precision 1.00 on 32 of 32 covered rows** at coverage 0.525.
That is the best-evidenced claim in round 2, and unlike every accuracy number here it did not move
when the instrument got harder. Coverage falls (0.692 → 0.525) because the arms diverge more on hard
tasks, which is the cascade behaving correctly.

Adding a third arm from a different family (`Qwen3.5-9B`, fixed in advance) turned the n=13 cascade
into a vote that beat every individual arm — **0.923 vs 0.846**, resolving 3 of 4 contested rows, with
the fourth abstaining on a row where one arm was *right* and the other two were wrong in two
different ways. **A majority vote discards a lone correct arm.** That result is **not** carried over
to n=61: arm C has not been run at an examined operating point, and running it at an unexamined one
would repeat the error of finding 14.

**What it learned is legible.** Parse rate went **0.92 → 1.00** while tool accuracy stayed at 0.92.
It did not learn *which* tool to call — the base already knew — it learned to emit the call correctly
and fill the arguments exactly. That is precisely the narrow, repetitive competence the paper claims
small models can absorb. It also generalised the one inference it was asked to: training says
*"terse" / "silently" / "keep it brief"*, the held-out set says *"quietly" / "no extra output"*.

**The asterisk that turned out to matter — and then to matter less.** Ornith's prose-interface score
was flagged on sight as untrustworthy: a tool accuracy of 0.69 for a *tool-tuned* model indicts the
prompt, not the model. Re-testing through the native `tools` parameter did move it, and finding 14
records that. But the *magnitude* I first published (+20 points) came from measuring a model
benchmarked at temperature 1.0 at temperature 0 instead. At its own operating point the format is
worth **+6.7 standard and +7.7 adversarial against run-to-run spreads of 0.15–0.23** — overlapping
distributions, not a clean effect. On the wide set the interface is worth 6 points while the gap to
the tuned model is 20: **format was a real effect and a small one, and I had inflated it by measuring
off-spec.**

---

## 4. The twenty-four findings the paper does not contain

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

**5.4× the compute on identical data cost 6.5 points.** The plateau is not starvation.

And the epoch-matched arm reached **train and validation loss 0.000** while being the worst
configuration tested. A practitioner selecting on validation loss picks exactly this model. Finding 4
said validation loss is not target accuracy; here it is *anti-correlated* with it at the decision
that matters.

It was also unfalsifiable in practice, for a reason worth more than the mechanism was: the per-tool
cells are **12 rows each**, and every movement in that table — predicted or actual — sits within ±2
of 12 across a 21× data range. **The instrument is adequate for aggregate claims at n=61 and
inadequate for per-tool claims at n=12**, and I made a per-tool claim from it. That is finding 18 in a
mirror — there, identical scores on a saturated instrument read as equivalence; here, moving scores on
an underpowered slice read as mechanism. Both are the instrument talking.

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
"declined to answer"; and the two arms fail on **disjoint** adversarial tasks, which makes them
complementary — an ensemble does not beat either on accuracy, but their agreement is a perfect trust
signal.

**15. A harness encodes the output conventions of whatever arm you built it against.** A third arm,
a reasoning model, scored **parse rate 0.00 on every task** and was additionally flagged unsound for
truncation. Both signals pointed at the model. Its actual reply was a perfectly correct call at 458
tokens, untruncated, sitting after a `</think>` block — and the grader's `re.compile(r"\{.*\}",
re.DOTALL)` is **greedy**, so it matched from the first brace in the reasoning preamble to the last
brace in the reply and handed `json.loads` one un-parseable blob.

The harness had been developed against a model that emits bare JSON. It silently encoded that
convention, and then scored every later arm partly on how closely it resembled the first one. Fixing
it moved **three published numbers, every one against a claim I had made**: the base model I was
beating rose 0.55 → 0.60 and 0.69 → 0.769, and fine-tuning's adversarial gain fell from +15.6 points
to **+7.7 — one task in thirteen**. The tuned arm and the native arm were unaffected, because the
adapter emits bare JSON and the native path never touches the parser. **Only the comparators were
penalised, which is the direction that flatters the conclusion.**

This is finding 14 one layer down — there the mismatched instrument was the prompt, here it is the
output parser. The rule that catches both: **when an arm scores zero, suspect the instrument before
the model.**

**16. Constrained decoding cuts run-to-run variance ~3×, but only where there is sampling to
constrain.** I first tested this at `temperature: 0` — comparing two *deterministic* paths, which
measures nothing, and which is why the claim looked unreproducible when probed again. Measured at
the temperature the model is actually benchmarked at, over three runs of each interface on 61 tasks,
it holds:

| Ornith at temp 1.0 | run-to-run spread |
|---|---|
| native `tools` | **0.049** |
| prose | 0.164 |

A tool grammar constrains the sampler so it cannot wander, which makes the native interface worth
using for **reproducibility** independently of accuracy. The corollary is the operational one: a
single prose run at spec is worth ±8 points, larger than several differences this project has
reported as results.

**A test can be run at a setting where the effect it targets cannot exist.** That is what the first
version of this finding did, and no amount of repetition would have revealed it.

**17. A permissive parser plus a non-terminating model equals a fabricated answer.** The third arm
failed one row not because its token budget was mean but because **it never stopped**. Probed at a
5,000-token cap it used all 5,000 and ended mid-sentence in a repetition loop — *"Wait, I'll check if
I should use `tests/telemetry`. Yes. Okay. Wait, I'll check if I should use `tests/"* — and it emitted
**eight draft tool calls** on the way. The parser dutifully harvested the last draft and the grader
scored it. That row's answer was never an answer; it was a fragment scraped out of a generation that
never finished, and raising the budget from 2,600 to 5,000 changed nothing because the loop is not a
budget problem.

This is the sting in finding 15's tail. Fixing the greedy regex was *necessary*, but it made the
parser more tolerant — and a more tolerant parser is precisely what converts a non-terminating run
into a plausible-looking score. The only thing separating that row from a clean 0.846 is the
truncation flag, so the run is published `sound: false` with the loop described rather than as a
number. **Every increase in parser tolerance has to be paid for with a soundness gate.**

**18. A tie on a saturated instrument is not a tie.** Round 2 concluded that conversion *ties*
selection, because the tuned 3.2 GB model and the 9.5 GB tool-tuned model both scored **1.00 on
n=20** and **0.846 on n=13**. Identical numbers, twice, on two different sets — which reads like
converging evidence and is in fact the opposite. Both arms were at the **ceiling** of instruments too
small and too easy to separate them. On a 61-row set built to have headroom they differ by **0.197**,
and the selected model does not catch up at any temperature.

The tie carried no information and was reported as a finding. Together with findings 14 and 15 the
pattern is now explicit — an instrument shaped for the wrong model, an instrument shaped for the
wrong output style, and an instrument with no headroom left. **Identical scores are evidence about
the instrument at least as often as evidence about the models**, and every number this project
reported from n=13 or n=20 should be read as a bound on uncertainty rather than a measurement.

**19. A model has an operating point, and it is not a free parameter.** Every served measurement in
round 2 hardcoded `temperature: 0`, silently overriding the inference server's own `--temp 0.6` on a
model its authors benchmark at **1.0**. Re-measured at spec, two published claims moved: the format
effect fell from +20 points to ~+7 against spreads of 0.15–0.23, and finding 16's determinism claim
inverted — at temp 0 the native interface was the stable one, at temp 1.0 it is the noisy one
(spread 0.150) while prose is stable (0.000).

Temperature was ruled in by diagnosis, not assumption: the request-level value **is** honoured
(temp 0 → 1 distinct reply in 4, 0.6 → 3, 1.5 → 2 with an outlier). It also turned out **not** to be
the explanation for the wide-set gap — 6 points of 20 — but that could only be established by
measuring it. Sampling configuration now travels in each result's arm metadata; the MLX arms are
still greedy by default rather than by argument, and that is recorded as an open gap rather than
quietly assumed to be fine.

**20. Fine-tuning a narrow subtask buys sampling robustness, not just accuracy.** Measured at both
operating points, the tuned adapter scores **0.820 greedy and 0.820** at its card point
(`temp 1.0 / top_p 0.95 / top_k 64`) — identical means, and **0.812 / 0.812** on the fresh slice. A
genuinely loose sampler barely moves it. The same sampler costs the *base* model accuracy and nearly
doubles its run-to-run spread:

| Arm | spread over 3 runs at temp 1.0 |
|---|---|
| E2B base | 0.115 |
| **E2B + tool adapter** | **0.066** |

QLoRA on 460 examples sharpened the output distribution enough that the model became **insensitive to
its own serving configuration**. That is worth more than it first appears: a converted model does not
need its sampling config to be right, and this project has now twice been damaged by exactly that
failure — `--repeat-penalty 0` in finding 12 and temperature in finding 19.

Together with finding 16 it gives two independent ways to sharpen a model's output distribution:
**fine-tune it, or constrain the decoder.** This round measured both, and neither would have been
visible from a single operating point.

**21. A mechanism inferred from one row is a story, not a finding.** `read_file` was the only tool
where fine-tuning *cost* accuracy — one row in twelve: *"Throw internal/auth/jwt.go on screen."*
answered as `write_file(path="screen", …)`. The training write templates are all **verb + thing +
preposition + place** (*"Park build 4172 in tmp/lock.pid"*), so I concluded the model had learned a
syntactic frame that overfires, recorded that mechanism **before** the data curve ran, and predicted
that more rows of the same shapes would entrench it.

Every part of the prediction failed. The row is **fixed at N=2,500 and N=10,000** and wrong at
460/1,000/5,000 — it flips rather than entrenching — and `search` and `write_file` *declined* with
scale instead of improving. The mechanism is wrong, and the pre-registration required writing that
down rather than reaching for a replacement.

The failure mode is specific and seductive: **the row was legible.** I could read the templates, see
the shared frame, and construct a causal account that explained the observation perfectly. Explaining
one data point perfectly is not evidence — it is overfitting, performed by me rather than by the
model.

**22. More compute on the same data overfits, and validation loss recommends it.** The curve's
plateau had an obvious alternative explanation — at a fixed 400 iters the large arms are compute-
starved, seeing 1,600 of 10,000 rows once. The pre-registered check was one epoch-matched arm: 2,500
examples at 2,175 iters (~3.5 epochs, the repetition N=460 received) instead of 400.

| Same 2,500 examples | compute | greedy |
|---|---|---|
| fixed compute | 400 iters (0.64 epochs) | **0.885** |
| epoch-matched | 2,175 iters (3.5 epochs) | 0.820 |

**23. A chat template mode is part of the operating point.** Finding 19 established that temperature
must be named and recorded. The template mode is the same class of thing and nothing covered it. The
second-family round served Qwen through its template's default **reasoning** mode while the training
data contained no reasoning at all, and the result was 1,272-token outputs, **31% truncation**, and —
worst — **11 of 19 truncated rows graded correct** because the parser scraped a draft call out of a
generation that never finished (finding 17's mechanism, at scale).

That run computed a conversion gain of **+0.098** against a pre-registered floor of 0.10. **The
harness would have concluded round 2's headline was substantially a Gemma artifact.** It is not: at
train/serve parity the gain is **+0.131** and both converted families land on exactly 0.885. The only
thing between the wrong conclusion and publication was `sound: False`.

The fix is one kwarg and the effect is not subtle — same model, same adapter, same task: **661 tokens
with thinking on, 21 with it off.** `apply_template` now records the mode, and deliberately records
`None` when the kwarg was never passed rather than reporting a mode as though it had been chosen —
inheriting the default *is* what went wrong.

**24. Fine-tuning changes what a model says, not the scaffold it says it inside.** The pre-registered
sub-question was whether QLoRA on 460 examples teaches a *reasoning* model to stop reasoning. It does
not. With thinking on, the **tuned** model emitted **1,272 tokens — more than the base's 1,222** —
after 400 iterations to a training loss of 0.000. The adapter had learned the format perfectly:
handed a template that lets it answer, it emits 21 tokens of clean JSON. It simply could not override
a block the template opens.

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
- **A "tie" — withdrawn as a ceiling artifact.** Two arms scored identically on n=20 and again on
  n=13; on n=61 they differ by 0.197. See finding 18.
- **This project's own headline — overturned by this project, then re-established.** "Conversion
  beats selection" fell when the selected model was re-measured through its own interface, and again
  when the grader stopped mis-scoring models that reason before answering. On a 61-row instrument
  with both arms at declared operating points it holds after all — by 0.20, unconditionally. Three
  measurements, three different answers, and only the last one had a usable instrument under it.
  See findings 14, 15, 18 and 19.

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
python evals/tool_call_eval.py --adapter evals/adapters/e2b_tools_clean --set wide
python evals/tool_call_native.py --model <served> --set wide --temperature 1.0  # at ITS spec
python evals/run_ensemble.py --set wide --arm <tuned>.json --arm <native>.json
```

639 tests, CI on ubuntu/macos/windows × py3.9/3.13. Eval datasets and adapters are gitignored;
tooling and results are committed.

---

## 8. Status

**Converted across two model families, gated, deployed.** The tuned 3.2 GB classifier is the router's
default. On a
61-task held-out set the tuned 3.2 GB tool-caller scores **0.820 against 0.623** for a purpose-built
9.5 GB tool-tuned model at its own best configuration — **conversion beats selection by ~0.20 at 34%
of the memory**, in-process, with no server. Where the two agree, they are right on **32 of 32**
covered rows. The pipeline is re-runnable end to end.

The reproduction is honest about running at ~5–7% of the paper's data scale, and about round 1
converting a classifier rather than an agent. Round 2's conclusions now rest on n=61 rather than
n=13; the earlier small-set numbers are superseded, not averaged in.

**Five of round 2's published numbers were later corrected by this project's own instruments, and
the corrections ran in both directions** — a fine-tuning gain inflated by a broken parser, then
deflated by a saturated one; a format effect inflated by an off-spec temperature; a "tie" that was a
ceiling. The twenty-four findings above are, in my judgement, worth more than either model, and most of
them are about instruments rather than models.
