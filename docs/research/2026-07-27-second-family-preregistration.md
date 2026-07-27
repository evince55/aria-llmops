# Second model family — PRE-REGISTRATION

**Model family** is the last open row in the write-up's fidelity table: the paper uses several, this
reproduction converted one (Gemma-4 E2B). Everything else is now closed or marked stronger than the
paper. This closes it with the only genuinely different family available locally,
**Qwen3.5-9B-MLX-4bit**.

## The question

Are round 2's results properties of **the pipeline**, or properties of **Gemma**?

Nothing so far distinguishes them. "Conversion beats selection", the sampling-robustness finding, and
the data plateau were all measured on one base model, and a reproduction that converts a single
family cannot tell a method from a lucky checkpoint.

## Protocol — inherited, not re-invented

Identical to the E2B arms in every respect except the base model: same subtask, same 460-example
dataset (`curve_460`), same schedule (400 iters, batch 4, rank 8, lr 1e-4, max_seq 768, mask_prompt),
same wide eval (n=61), same two operating points. Deviating anywhere would make the comparison a
different experiment rather than a replication.

## The sub-question worth as much as the main one

Qwen is the arm that produced **finding 17**: it never terminated on one task, running to a
5,000-token cap in a repetition loop while emitting eight draft tool calls, which a permissive parser
then harvested as an answer. It is a reasoning model asked to answer in one line.

**Does QLoRA on 460 examples teach a reasoning model to stop reasoning?** Measured concretely, not
impressionistically:

- `truncation_rate` and the `sound` flag — does the non-termination disappear?
- mean output length, base vs tuned — the direct measure of preamble suppression.

## Predictions, and deliberately few of them

Finding 21 was earned by building a detailed mechanism on a single row. So only what the instrument
can actually support at n=61:

1. **If the pipeline is what works**, Qwen-tuned beats Qwen-base by a margin comparable to E2B's
   (+0.34 on wide at N=460). A much smaller gain means the method depends on the base.
2. **Tuning suppresses the reasoning preamble**, so the tuned arm is sound where the base was not.

No per-tool predictions. The per-tool cells are 12 rows and finding 21 is what happens when I forget
that.

## What would falsify what

- Qwen-tuned gaining **< 0.10** over Qwen-base would mean round 2's headline is substantially a
  Gemma result, and the write-up must say so.
- Qwen-tuned still hitting the token cap would mean fine-tuning does **not** suppress reasoning at
  this data scale — a finding, not a failure, and it gets reported either way.
- If the *base* Qwen arm now scores far from its earlier reading, the earlier reading was noise;
  it was taken on n=13 and flagged unsound at the time.

---

## AMENDMENT (2026-07-27, before any comparison was run): the schedule had to change

**The inherited schedule crashed the machine.** Applying the E2B schedule unchanged to a 9B model
demanded **25.7 GB peak on a 16 GB Mac** — a 1.6× overcommit, so the machine swapped itself to death.
The run was also already worthless: train loss at iter 100 read `42949672.960`, which is exactly
`2³²/100`, an overflow sentinel rather than a loss.

**What I got wrong.** "Protocol inherited, not re-invented" is a claim about the *experiment*. It is
not a claim about the *resource envelope*, and batch size and gradient checkpointing are resource
parameters whose memory cost scales with model size. Holding them fixed protected nothing and cost
the machine.

### The new schedule, and which changes are confounds

| change | resource or scientific? | evidence |
|---|---|---|
| `--grad-checkpoint` on | **resource** | recomputes activations; mathematically identical result |
| `max_seq 768 → 512` | **resource** | longest training row is **137 tokens**; nothing truncates |
| `batch 4 → 1` | **scientific** | changes gradient noise and optimisation |
| `lr 1e-4 → 1e-5` | **scientific** | changes optimisation |

The first two were verified rather than assumed — the sequence-length check was run, not reasoned
about. The last two are genuine confounds, so:

**Both arms are retrained under the identical new schedule.** Comparing a Qwen trained one way to an
E2B trained another would be exactly the "measured through the wrong instrument" error findings 14,
15 and 19 are about. Schedule B is: `batch 1, seq 512, lr 1e-5, grad-checkpoint, 1600 iters` — 1,600
iterations at batch 1 keeps **examples seen** equal to the curve's 400 × 4, so the training budget is
preserved even though the schedule is not.

Probe at the new settings: peak **7.09 GB**, val loss 0.994 → 0.004, train loss 0.313 → 0.024. Both
failures resolved.

### What this costs the comparison to the curve

The E2B number produced here is **not** directly comparable to the curve's 0.853 — different batch
size and learning rate. It is comparable to the Qwen number produced beside it, which is the only
comparison this round is making. The curve stands on its own arms.
