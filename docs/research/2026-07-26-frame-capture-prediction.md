# Frame capture — a mechanism, and a prediction recorded before the curve runs

**Written while the curve adapters are still training, deliberately.** The best thing in this
project's write-up is the S6→S7 loop: an audit predicted a specific data defect, the prediction was
recorded and ignored, the gate later measured its cost at −18 points, and fixing it recovered +21.
Predicted, measured, fixed, re-measured. This is an attempt to run that loop on purpose.

## The observation

`read_file` is the only place fine-tuning **cost** accuracy on the fresh slice: base 11/12, tuned
10/12. One row, and it is legible:

| | |
|---|---|
| task | `Throw internal/auth/jwt.go on screen.` |
| expected | `read_file(path="internal/auth/jwt.go")` |
| **base** | `read_file(path="internal/auth/jwt.go")` ✓ |
| **tuned** | `write_file(path="screen", content="internal/auth/jwt.go")` ✗ |

## The mechanism

Every `write_file` training template has the same shape — **verb + thing + preposition + place**:

```
Park build 4172 in tmp/lock.pid
Scribble batch 88 onto jobs/cursor.txt
Record {c} at {p}
Drop {c} into the file {p}
```

`Throw X on screen` fits that frame exactly. The tuned model bound `X → content` and
`screen → path` and emitted a write. The base model, which never learned the frame, read *"on
screen"* semantically and answered correctly.

**Fine-tuning did not make the model worse at reading files. It learned a syntactic frame, and the
frame overfires on a held-out task that shares the frame while carrying a different intent.** That is
a specific, mechanistic cost of training on templated data, and it is invisible in an aggregate score
— it shows up as one row in twelve.

## The prediction, recorded now

The curve scales **vocabulary** while holding **templates** fixed, so every one of the 10,000 rows at
the largest size is one of the same handful of frames with different nouns in the slots. If frame
capture is the mechanism, then:

1. **More rows of the same shapes will not fix this row, and may entrench it.** The frame gets more
   evidence, not less; nothing in the added data teaches that *"on screen"* is a destination for
   attention rather than a filesystem path.
2. **The tools whose gains come from argument precision** — `search`, `write_file` — **should keep
   improving with scale**, because there the added vocabulary is genuinely new evidence about how to
   fill a slot.
3. So the curve should show **per-tool divergence**: `read_file` flat or slightly worse, others
   improving. An aggregate plateau would then be two effects cancelling, not one effect absent.

**If prediction 1 fails and the row is fixed at N=10,000, the mechanism above is wrong** and frame
capture is not what is happening — write that down rather than reaching for a second explanation.

## Why this matters beyond one row

It sharpens what the data curve can and cannot conclude. A plateau produced by frame capture is a
statement about **templated data generation**, not about small models' data appetite — and this
reproduction would be overclaiming if it reported the former as the latter. The pre-registration
already flags template-fixed scaling as a confound; this names the *mechanism* by which that confound
would operate, and makes it falsifiable per tool rather than in aggregate.
