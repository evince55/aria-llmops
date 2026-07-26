# S10 ensemble — the pre-registered rules held, and a harness bug moved every prose number

**Date:** 2026-07-26 · **Rules fixed in advance:** `2026-07-26-s10-ensemble-preregistration.md`

The ensemble ran as pre-registered and both predictions were confirmed. Getting there turned up a
harness bug that had been silently mis-scoring every prose arm, and a stack property that makes one
of my earlier point estimates less solid than I reported it.

---

## 1. The bug: a greedy regex scored a reasoning model at 0.00

Arm C (`Qwen3.5-9B`) came back **parse rate 0.00 across all 13 tasks** — and flagged
`sound: false`, because 38% of rows had also hit the token cap. Both signals pointed at the model.
Both were wrong.

Its actual reply, at 458 tokens and untruncated:

```
Thinking Process:
1. Constraint: reply with ONE tool call as `{"tool": "<name>", "args": {...}}`.
2. `read_file(path: str)` reads a file, so it is the right tool.
</think>

{"tool": "read_file", "args": {"path": "vendor/lib.rs"}}
```

A perfectly correct call. `parse_call` used `re.compile(r"\{.*\}", re.DOTALL)` — **greedy** — which
matches from the *first* brace in the text to the *last*. The model echoes the schema while
reasoning, so the match spanned the entire reply as one un-parseable blob and `json.loads` failed.

The fix scans with `JSONDecoder.raw_decode` at each `{`, keeping the last valid call. Brace counting
was tried first and rejected: a depth counter never recovers from an *unmatched* `{` in a reasoning
preamble, which is easy to emit while discussing code.

**Why this matters beyond one arm.** The harness was developed against a model that emits bare JSON,
so it silently encoded that assumption, and every model with a different output style was penalised
for it. That is the same failure as finding 14 — measuring a model through an instrument shaped for a
different model — one layer further down. There, it was the prompt format; here, the output parser.

### What the fix changed

| Arm | Interface | standard: was → now | adversarial: was → now |
|---|---|---|---|
| E2B base | prose | 0.55 → **0.60** | 0.69 → **0.769** |
| E2B tuned | prose | 1.00 → 1.00 | 0.846 → 0.846 |
| Ornith | prose | 0.85 → **0.80** ± .05 | 0.62 → **0.538** |
| Ornith | native | 1.00 → 1.00 | 0.846 → 0.846 |

The tuned and native arms are unaffected — the adapter emits bare JSON, and the native path never
touches `parse_call` at all. **Only the arms I was comparing *against* were penalised**, which is the
direction that flatters the conclusion.

### The correction that costs the most

**Fine-tuning's adversarial gain drops from +15.6 points to +7.7 — one task out of thirteen.**

| Claim | Published | Corrected |
|---|---|---|
| fine-tune gain, standard | +45 | **+40** |
| fine-tune gain, adversarial | +15.6 | **+7.7 (1 task)** |

On the standard set the fine-tune result stands. On the adversarial set — the one I called "the
honest estimate of generalisation" — it is now a single task, which n=13 cannot support. A related
detail I had not reported: on the adversarial set the **base** model gets *every* tool right
(tool accuracy 1.00) while the **tuned** model gets 0.923. Fine-tuning bought argument precision and
cost a little tool selection.

---

## 2. The stack: prose is nondeterministic at temperature 0, native is not

Re-running Ornith through prose gave a different score than before, in the direction the parser fix
could not explain. Same model, same endpoint, same prompt, `temperature: 0` — and a substantive flip:

```
Just run the api tests, no extra output.
   run 1:  {"tool": "run_tests", "args": {"target": "api", "verbose": false}}   correct
   run 2:  {"tool": "run_tests", "args": {"target": "api", "verbose": true}}    wrong
```

So I measured it. Four runs of each interface on the standard set:

| Interface | runs | strict accuracy | spread |
|---|---|---|---|
| prose | 4 | 0.75, 0.80, 0.80, 0.85 | **0.10** |
| native `tools` | 4 | 1.00, 1.00, 1.00, 1.00 | **0.00** |

The MLX arms are deterministic (E2B base 0.600 ×3, E2B tuned 0.846 ×2), so this is a property of the
llama.cpp server, not of the harness. **Constrained decoding removes run-to-run variance**: with a
tool grammar the sampler cannot wander, and both the score *and the specific failing rows* are
identical every time.

**What this costs the earlier claim.** I reported the format effect as "0.85 → 1.00 = 15 points" from
single runs. Against the measured prose mean it is **0.80 → 1.00 = 20 points**, and the honest
statement is that the effect (20) is larger than the noise (10) rather than that it is exactly 20.
The direction and the conclusion survive; the precision I implied did not exist.

For an eval instrument, reproducibility is worth as much as accuracy. A single prose run is worth
±5 points; a single native run is worth exactly itself.

---

## 3. The ensemble: both predictions confirmed

Rules and arm C were fixed **before** running anything, because I had already seen both arms'
row-level failures and a rule invented afterwards would be fitted to 13 examples.

The failures are disjoint under the corrected parser, and stable: Ornith-native fails the *same two
rows* on every run, and neither is a row E2B fails.

### Rule A — agree-or-escalate

| Set | coverage | precision on covered | escalation | end-to-end accuracy |
|---|---|---|---|---|
| adversarial (n=13) | 0.692 | **1.00** | 0.308 | 0.692 |
| standard (n=20) | 1.00 | 1.00 | 0.00 | 1.00 |

**P1 confirmed** — where the two arms agree, they are right, 9 times out of 9.
**P2 confirmed** — agreement covers 9/13; the other 4 are exactly the disagreements.

**The end-to-end number is worse than either arm alone** (0.692 vs 0.846), and that is not a
disappointment — it is what the pre-registration said would happen. Two arms with no tiebreaker
cannot vote, so the cascade cannot raise accuracy. What it buys is a **perfect trust signal**: when
these two models agree you can ship the call unchecked, and when they disagree you are escalating on
precisely the rows where one of them is wrong. For a router that is directly usable — 69% of tool
calls served by a 3.2 GB model with zero errors, 31% escalated.

The standard-set row is the null check: both arms score 1.00 there, and the cascade correctly finds
zero disagreements rather than manufacturing escalations.

### Rule B — 3-arm majority vote

Arm C is `Qwen3.5-9B`, fixed in the pre-registration and not swapped when it first scored 0.00.

| Rule | accuracy | abstain |
|---|---|---|
| each arm alone | 0.846 (11/13) | — |
| **majority of 3** | **0.923 (12/13)** | 0.077 |

**P3 confirmed.** The vote resolves **3 of the 4 contested rows** and beats every individual arm.
That is the payoff the disjoint-failure observation predicted, and it is worth exactly one task.

| Contested row | E2B tuned | Ornith native | Qwen (arm C) | vote |
|---|---|---|---|---|
| `Cat out bin/run` | ✗ `write_file` | ✓ | ✓ | **resolved** |
| `TODO(bug) in the shell files` | ✓ | ✗ regex-escaped | ✓ | **resolved** |
| `Emit release candidate 4 to …` | ✓ | ✗ `search` | ✓ | **resolved** |
| `css files contain !important` | ✗ `pattern="!"` | ✓ | ✗ `glob="**/*.css"` | abstain |

**The abstention is the interesting one, because a correct answer was thrown away.** On the
`!important` row Ornith was *right* and the other two were wrong in two different ways, so no
majority formed. A vote cannot rescue a lone correct arm — it discards it. Majority voting trades
that away for protection against a lone *wrong* arm, and on this set the trade paid 3-for-1.

Arm C also earns its keep on independence rather than strength: it is the only arm from a different
model family, and it broke the tie on three rows where the two primary arms split.

### Arm C's own run is reported UNSOUND, and the reason is a finding

Arm C scores **0.846 with 1 of 13 rows unmeasurable**. That row does not truncate because the budget
was mean — it truncates because **the model never terminates**. Probed directly at a 5,000-token cap
it uses all 5,000 and ends mid-sentence in a repetition loop:

> `…Wait, I'll check if I should use `tests/telemetry`. Yes. Okay. Wait, I'll check if I should use `tests/`

Re-running the whole set at 5,000 tokens instead of 2,600 changed **nothing** — same score, same rows,
same calls — because the model is looping on the same draft either way.

**The trap: it emitted 8 draft tool calls inside that loop, and the parser harvested the last one.**
The graded answer for that row was never an answer; it was a fragment scraped out of a generation
that never finished. Note the interaction with §1 — the parser fix was *necessary*, but it made the
parser more permissive, and a more permissive parser is exactly what turns a non-terminating run into
a plausible-looking score. **The only thing standing between that and a clean 0.846 is the truncation
flag**, which is why `score()` refuses to mark the run sound and why the number is published with
this caveat attached rather than without it.

Rule B does not depend on the unsound row: it is not one of the four contested rows, and it already
had a 2-of-3 majority from the other arms. The sound and unsound runs give the same 0.923.

---

## 4. What this round says about the method

Three numbers I had published moved, and **every one of them moved against a claim I had made**:
the baseline I was beating was understated, the gain I attributed to fine-tuning shrank to a single
task, and a point estimate I quoted to two significant figures had ±5 points of stack noise under it.

None of it was caught by a test. All three surfaced from following the pre-registration honestly —
committing to arm C before seeing its score meant I had to explain a 0.00 instead of quietly
swapping the arm, and explaining it found the bug.

**The generalisable rule: when an arm scores zero, suspect the instrument before the model.** A
harness is built against whatever arm you developed it with, and it silently encodes that arm's
output conventions. Every later arm is then scored partly on how closely it resembles the first one.
That is finding 15.

---

## 5. Next

- The adversarial set is n=13 and **4 rows carry the entire ensemble signal**. Widening it is now the
  highest-value work in this line; nothing above should be treated as a decision.
- Re-run the standard-set arms enough times to publish means with spreads rather than point
  estimates, at least for anything served through llama.cpp.
- The 2-arm cascade's escalation trigger is cheap and real. Wiring it into the router is a
  self-contained next step.
