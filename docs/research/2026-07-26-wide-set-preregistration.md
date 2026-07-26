# Widening the adversarial set — PRE-REGISTRATION (before any task was authored)

n=13 with **four rows carrying the entire ensemble signal** is the stated weakness of every round-2
number. This widens it. Committed before a single new task was written, for one reason:

**I have already seen exactly which rows each arm fails.** E2B misparses `Cat out bin/run` as a
write and truncates `!important` to `!`; Ornith regex-escapes `TODO(bug)` and reads `Emit … to <path>`
as a search; Qwen prefers `**/*.css` and loops forever on `full-chatter`. If I now author 50
"adversarial" tasks freehand, I will drift toward the shapes that discriminate, and the widened set
will measure my memory of the failure table rather than the models.

## Two slices, kept separate and reported separately

**FRESH (the generalisation instrument).** Authored against the structural axes below, which are
fixed *now* and derived from properties of the task surface — not from any observed failure.

**REGRESSION (the debugging instrument).** Tasks deliberately targeting known failure modes. These
are legitimate and useful, but they are **fitted to observed failures by construction** and must
never be used to support a generalisation claim. Reported as their own number, never pooled into
a headline.

Any conclusion about generalisation comes from FRESH alone.

## The axes, fixed now

Each is a property of the tool surface, declared before authoring:

1. **Path shape** — nested depth, dotfiles, extensionless, hyphen/underscore, uncommon extensions.
2. **Verb distance** — request verbs far from the training templates' verbs.
3. **Boolean inference** — verbosity expressed through idiom rather than the words `verbose`/`quiet`.
4. **Content shape** — multi-word, digit-bearing, mixed-case, punctuation-bearing contents.
5. **Sentence form** — question, polite request, bare imperative, embedded clause.
6. **Distractor mention** — the task names a noun belonging to a *different* tool than the one
   required (e.g. mentioning tests while asking for a file read). Structurally adversarial: it
   attacks keyword-shaped shortcuts rather than any failure I happened to observe.

Target: **12 fresh tasks per tool, 48 total**, spread across all six axes.

## Rules the new tasks must satisfy — checked in code, not by intention

- `validate()` passes (every call matches the declared tool surface).
- Disjoint from `TASKS` and from the existing `HARD_TASKS`, by task text.
- No path, target, pattern or content value reused from `TASKS` or from the training generator's
  vocabulary — a memorised filename must not be able to score.
- `phrasing_overlap()` finds nothing: no held-out wording reachable from a training template.
  Finding 13 was exactly this, and it passed exact-match quarantine while failing.

## One ambiguity I am deliberately NOT introducing

Two of the four contested rows in the n=13 set are arguably grader artifacts rather than model
errors: a regex-escaped `TODO\(bug\)` and a `**/*.css` glob are defensible readings of an
underspecified schema. The prompt does not say whether `pattern` is literal or a regex, nor what
glob style is expected.

**The fresh tasks avoid regex metacharacters in patterns and use one consistent glob style**, so the
widened set does not multiply an existing dispute by four. Disambiguating `SCHEMA_PROMPT` itself
would change the task for every arm and invalidate all prior numbers; that is a separate decision
and is recorded as one, not folded in here.

## A limitation I cannot design away

These tasks are **author-written**, not harvested from real usage — the same weakness the n=13 set
has. This project has already established that a model-generated eval inflates models trained on
model text, and I am a model writing eval tasks. The axes and the FRESH/REGRESSION split constrain
the bias; they do not remove it. A genuinely independent instrument would need tasks harvested from
real tool-call traffic, which does not exist for this surface yet.

## What would falsify the widening

If arm scores on FRESH land far from their n=13 scores, the n=13 numbers were noise and should be
withdrawn rather than averaged with the new ones. That is the expected outcome for at least one arm
and it is the point of doing this.
