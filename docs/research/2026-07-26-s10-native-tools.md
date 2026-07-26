# S10 addendum — the format was the whole gap

**Date:** 2026-07-26 · **Revises:** the Ornith column of `docs/REPRODUCTION.md` §3
**Result: the "conversion beats selection" claim does not survive a fair test.** It is a tie on
accuracy, and conversion's win is memory, not capability.

## Why this run existed

S10 measured Ornith-1.0-9B — a *tool-tuned* model — by asking it for freeform JSON in prose, and
read 0.85 standard / 0.62 adversarial. The failures decomposed as 2 unparseable, 2 wrong tool,
1 wrong args. A tool accuracy of 0.69 for a model fine-tuned on tool use indicts the prompt, not
the model, so the S10 write-up recorded that column as **indicative, not a verdict** and filed the
re-test. This is the re-test.

Ornith is trained against the OpenAI `tools` parameter, where the call is emitted through a
constrained decoding path instead of being written out as prose.

## What was held constant

Same four-tool surface, same held-out tasks, same `grade()`. The single variable is the delivery
mechanism. The surface equivalence is **asserted in tests against `TOOLS`**, not eyeballed — a
schema that quietly dropped `verbose` or made an argument optional would hand the native arm an
easier task, and the comparison would be rigged in its favour. Both arms are re-serialised to one
shape before grading, so a scoring difference cannot explain a score gap.

The runner refuses to report a number if the server does not actually honour `tools`: a llama.cpp
build without `--jinja` accepts the parameter and silently ignores it, which would re-measure prose
and look like a result.

## The format was worth 15 and 23 points

| Ornith-1.0-9B | standard (n=20) | adversarial (n=13) |
|---|---|---|
| prose interface (S10) | 0.85 | 0.62 |
| **native `tools` interface** | **1.00** | **0.85** |

Parse rate went **0.90 → 1.00** on the standard set. The 2 unparseable replies that anchored the
original reading were an artifact of asking a constrained-decoding model to write prose.

`tool_choice: "auto"` and `"required"` returned **identical scores on both sets** — Ornith always
elects to call a tool when one fits. So the gap was never "declined to answer"; it was purely
format.

## The head-to-head, corrected

| Arm | Interface | Size | Standard | Adversarial |
|---|---|---|---|---|
| E2B base | prose | 3.2 GB | 0.55 | 0.69 |
| **E2B + tool adapter** | prose | **3.2 GB** | **1.00** | **0.85** |
| **Ornith-1.0-9B** | **native** | 9.5 GB | **1.00** | **0.85** |

**A dead tie on both sets.** S10's headline — *a tuned 3.2 GB model beat a selected 9.5 GB
tool-tuned model* — was an artifact of measuring the selected model through the wrong interface.
The corrected claim is narrower and still worth something: **a 3.2 GB QLoRA-tuned generalist
matches a purpose-built 9.5 GB tool model at 34% of the memory, in-process, with no server.**

That is arguably a *better* result for the paper's thesis than the one it replaces. The paper's
claim is that small models suffice for narrow agentic subtasks — not that fine-tuning beats
selection. A tie against a specialist three times the size supports the claim; a spurious win
against a mis-measured one supports nothing.

## The errors are uncorrelated, which is the more useful finding

Both arms score 11/13 on the adversarial set and **fail on disjoint tasks** — four disagreements,
two each way:

| Task | E2B tuned | Ornith native |
|---|---|---|
| `Cat out bin/run` | ✗ called `write_file`, content `"cat"` | ✓ |
| `Do the css files contain !important anywhere?` | ✗ pattern `"!"` | ✓ |
| `Any occurrences of TODO(bug) in the shell files?` | ✓ | ✗ pattern `TODO\(bug\)` |
| `Emit release candidate 4 to build/tag.txt.` | ✓ | ✗ called `search` |

Zero overlap in failures means an ensemble or a cascade would outscore either arm — the two models
are not making the same mistake, they are making different ones. For a router, that is directly
actionable: these are complementary, not redundant.

**One of Ornith's two misses is arguably not a miss.** It escaped the parentheses in `TODO(bug)` to
`TODO\(bug\)` — correct behaviour for a search tool that takes a regex, wrong against a ground truth
that expects a literal. The grader is deliberately literal and I am not going to loosen it after
seeing the results, but the honest reading is **1 genuine error + 1 defensible disagreement** for
Ornith against **2 genuine errors** for E2B. Credit the escape and Ornith is ahead 12–11.

## What this says about the method

The caveat did its job. S10 could have shipped "converted beats selected" as a clean headline —
the number supported it, and nothing in the harness would have objected. It was flagged as
unreliable because the *failure decomposition* was implausible for a tool-tuned model, and the
re-test overturned it.

**A number that is inconsistent with what you know about the system under test is a finding, not a
result.** Ornith's 0.69 tool accuracy was that; it was recorded as suspect, and it was wrong.

## Next

- Ensemble the two arms on the adversarial set — disjoint failures predict a gain, and it is free
  to test.
- The adversarial set is n=13. Two disagreements each way is four data points; widen it before
  anything is concluded about *which* model is better at what.
