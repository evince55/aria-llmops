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


def outcome_from_user_texts(user_texts: list[str]) -> Optional[str]:
    """Return the LAST decisive success/failure signal across ordered user turns,
    or None if none is confident."""
    verdict: Optional[str] = None
    for txt in user_texts:
        t = (txt or "").lower()
        # Failure phrases are checked first so that a turn containing both an
        # complaint and the word "merge" isn't mis-scored; but a *later* clean
        # success turn still overrides an earlier failure (loop continues).
        if _FAILURE_RE.search(t):
            verdict = "failure"
        elif _SUCCESS_RE.search(t):
            verdict = "success"
    return verdict


def outcome_from_transcript(lines: list) -> Optional[str]:
    """Derive a session outcome from parsed transcript objects (Claude Code JSONL).
    Uses user-turn reactions only (highest precision for a v1)."""
    from telemetry.ingest_claude_code import _content_to_text  # local import; avoid cycle
    texts = [
        _content_to_text(o.get("message", {}).get("content"))
        for o in lines
        if o.get("type") == "user"
    ]
    return outcome_from_user_texts([t for t in texts if t and t.strip()])
