"""Best-effort per-session OUTCOME inference from Claude Code transcript signals.

This is a HEURISTIC v1, not ground truth. The strongest cheap signal we have is
the *user's own reactions* to the assistant's work, so we scan the user turns for
decisive approval/rejection phrases and return the LAST decisive one — because a
session that starts "it's broken" and ends "works now, merge" is a **success**
(the issue was fixed), not a failure.

Returns "success" / "failure" / None. **None means "no confident signal"** — we
deliberately do NOT guess, so downstream analysis can treat labelled sessions as
a high-precision subset rather than assume every session has a verdict. As real
labels accumulate this heuristic can be replaced/augmented with a manual tag or a
model-graded outcome (see the 2026-07-01 routing/eval review).
"""
from __future__ import annotations

import re
from typing import Optional

# Decisive user-reaction phrases. Order within a session matters (we take the
# last match); across these sets, a later match of either polarity wins.
_SUCCESS = (
    r"works now", r"it works", r"works fine", r"works perfectly", r"works as intended",
    r"that works", r"this works", r"perfect", r"\blgtm\b", r"looks good", r"ship it",
    r"merge it", r"go ahead and merge", r"\bmerge\b", r"fantastic", r"resolved",
    r"fixed now", r"no issues", r"no more issues", r"working now", r"great,? merge",
)
_FAILURE = (
    r"still broken", r"still does ?n'?t", r"still not working", r"still not",
    r"does ?n'?t work", r"do ?n'?t work", r"not working", r"still fail", r"still errors?",
    r"that did ?n'?t", r"\bregression\b", r"broke ", r"is broken", r"still no ",
    r"did ?n'?t fix", r"wrong output", r"incorrect",
)
_SUCCESS_RE = re.compile("|".join(_SUCCESS))
_FAILURE_RE = re.compile("|".join(_FAILURE))

# Success phrases are approval WORDS, but approval can be negated: "don't
# merge", "never merge to main", "not perfect". Pre-guard, every one of those
# scored SUCCESS — instructions ("never merge to main directly") were read as
# the user approving a merge. A success match only counts if the ~32 chars
# before it don't end in a negator (with up to two words between negator and
# phrase: "don't you merge", "do not immediately merge it").
_NEGATION_TAIL = re.compile(
    r"(?:\bnot\b|\bnever\b|\bdon'?t\b|\bdo\s+not\b|\bwon'?t\b|\bwill\s+not\b|"
    r"\bdidn'?t\b|\bdid\s+not\b|\bdoesn'?t\b|\bdoes\s+not\b|\bcannot\b|\bcan'?t\b|"
    r"\bshouldn'?t\b|\bshould\s+not\b|\bwouldn'?t\b)\s*(?:\w+\s+){0,2}$"
)


def _has_unnegated_match(pattern: "re.Pattern", text: str) -> bool:
    """True if `pattern` matches somewhere in `text` NOT preceded by a negator."""
    for m in pattern.finditer(text):
        window = text[max(0, m.start() - 32):m.start()]
        if not _NEGATION_TAIL.search(window):
            return True
    return False


def outcome_from_user_texts(user_texts: list[str]) -> Optional[str]:
    """Return the LAST decisive success/failure signal across ordered user turns,
    or None if none is confident."""
    verdict: Optional[str] = None
    for txt in user_texts:
        t = (txt or "").lower()
        # Failure phrases are checked first so that a turn containing both an
        # complaint and the word "merge" isn't mis-scored; but a *later* clean
        # success turn still overrides an earlier failure (loop continues).
        # (Failure phrases embed their own negation — "doesn't work" — so the
        # negation guard applies to the success side only.)
        if _FAILURE_RE.search(t):
            verdict = "failure"
        elif _has_unnegated_match(_SUCCESS_RE, t):
            verdict = "success"
    return verdict


# Model grader — augments the keyword heuristic's LOW recall by reading the user
# reactions holistically. Only consulted when the keyword pass is inconclusive; it
# never overrides a confident keyword verdict, and any error/UNCLEAR -> None. On a
# 7-case probe the 9B grader scored 5/7 vs the keyword heuristic's 3/7 (2026-07-01).
#
# CRITICAL precision guard: the model must grade the user's REACTIONS to completed
# work, NOT the task framing. Two failure modes we saw the 9B fall into on the real
# ledger (2026-07-04) and defend against here:
#   1. Single-message sessions are just the opening request ("Harden data
#      durability…", "…is a latent footgun") — no reaction exists yet. The model
#      read the problem statement as a negative verdict. Fix: strip the framing turn
#      and require a real reaction to remain, else None.
#   2. Harness-injected pseudo-user turns (skill preambles beginning "Base directory
#      for this skill:") aren't the user talking. Fix: filter them out.
# Plus a stricter prompt: FAILURE requires explicit dissatisfaction, and neutral
# approvals ("sure", "proceed") are SUCCESS/UNCLEAR, never FAILURE.
_GRADE_PROMPT = (
    "A user is working with an AI coding assistant. Below are ONLY the user's "
    "follow-up messages AFTER the initial request — i.e. their reactions to work the "
    "assistant already did. Did the work ultimately SUCCEED or FAIL, or is it UNCLEAR?\n"
    "Rules:\n"
    "- FAILURE requires the user to explicitly say the work is wrong/broken/unwanted "
    "(and NOT later resolved). Do NOT infer failure from the task merely being about a "
    "bug or hardening — that is the goal, not a complaint.\n"
    "- Neutral approvals or continuations ('sure', 'proceed', 'yes', 'merge', 'add to "
    "obsidian') are SUCCESS or UNCLEAR, never FAILURE.\n"
    "- Weigh the LAST decisive reaction most (an early complaint fixed later = SUCCESS).\n"
    "- If there is no clear reaction to completed work, answer UNCLEAR.\n"
    "Reply with ONE word: SUCCESS, FAILURE, or UNCLEAR.\n\nMessages:\n{msgs}"
)

# Harness-injected turns that carry a user role but aren't the user speaking:
# skill preambles, background-task notifications, and context-compaction banners.
_INJECTED_PREFIXES = (
    "base directory for this skill:",
    "<task-notification>",
    "this session is being continued from a previous conversation",
    "caveat: the messages below were generated by the user while running",
)


def _reaction_texts(user_texts: list[str]) -> list[str]:
    """The turns that could be REACTIONS to completed work: drop harness injections,
    then drop the first surviving turn (the framing request, never a reaction)."""
    real = [t for t in user_texts
            if t and t.strip()
            and not t.strip().lower().startswith(_INJECTED_PREFIXES)]
    return real[1:]  # everything after the opening request


def _model_grade(user_texts: list[str], complete) -> Optional[str]:
    """Ask an injected `complete(prompt, max_tokens) -> str` for a verdict.
    Returns 'success'/'failure', or None for UNCLEAR / unparseable / any error.
    Grades only genuine reaction turns (see _reaction_texts) so a bare opening
    request is never scored as a failure."""
    reactions = _reaction_texts(user_texts)
    if not reactions:
        return None  # no reaction to grade -> no confident label
    msgs = "\n".join(f"- {t.strip()[:300]}" for t in reactions)[:4000]
    try:
        raw = (complete(_GRADE_PROMPT.format(msgs=msgs), 6) or "").strip().upper()
    except Exception:
        return None
    if "FAIL" in raw:
        return "failure"
    if "SUCCESS" in raw:
        return "success"
    return None  # UNCLEAR / unparseable -> no confident label


def grade_outcome(user_texts: list[str], complete=None) -> Optional[str]:
    """Keyword heuristic first (high precision); if inconclusive AND a model
    `complete` callable is provided, ask the model (raises recall). The model only
    augments — it never overrides a confident keyword verdict, and returns None on
    UNCLEAR/error so labelled sessions stay a high-precision subset."""
    kw = outcome_from_user_texts(user_texts)
    if kw is not None:
        return kw
    if complete is not None:
        return _model_grade(user_texts, complete)
    return None


def outcome_from_transcript(lines: list, complete=None) -> Optional[str]:
    """Derive a session outcome from parsed transcript objects (Claude Code JSONL).
    Keyword-only by default; pass a `complete` callable to also use the model grader
    for sessions the keyword pass can't decide."""
    from telemetry.ingest_claude_code import _content_to_text  # local import; avoid cycle
    texts = [
        _content_to_text(o.get("message", {}).get("content"))
        for o in lines
        # non-dict entries (a bare JSON string/array line) crashed this with
        # AttributeError pre-fix; tolerate them like the ingest parser does
        if isinstance(o, dict) and o.get("type") == "user"
    ]
    return grade_outcome([t for t in texts if t and t.strip()], complete=complete)
