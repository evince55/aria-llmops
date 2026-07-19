"""Harvest REAL, human-written task prompts for a held-out evaluation set.

Why this exists: the S6 gate showed that a model-generated eval set inflates a
model-trained-on-model-text. `labeled_tasks_balanced.jsonl` (source `35b-gen`)
scored 0.92 for the tuned E2B while the human union scored 0.738 -- not
contamination, just distribution match, since both the eval and the training
data were model-written. An honest instrument has to be written by a human.

We cannot synthesize human text, so we harvest it: the operator's own prompts,
already on disk in the route-decision telemetry and the session transcripts.

Two invariants this module protects:

1. **Labeler independence.** Nothing here labels. Labels must come from models
   that are NOT under evaluation -- never the keyword classifier, the 9B, or a
   tuned E2B, all of which are router components. Grading a router with labels
   its own components produced measures agreement, not accuracy.

2. **Train/eval disjointness.** Anything already in the training set is dropped,
   on normalized text, before a task can become an eval row.

PRIVACY: these are real operator prompts and this repo is public. `scrub()`
removes home paths, private-range and Tailscale IPs, emails, and hostnames
before anything is written. Scrubbing is applied at harvest time, not at
publish time, so an unscrubbed row never reaches disk.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Length band: below this a message is conversational ("proceed", "do both").
# The ceiling is 2000 because that is exactly where the production classifier
# truncates a task (``_CLASSIFY_PROMPT.format(task=task[:2000])``) -- text past
# it never reaches the router, so it cannot belong in an eval row. Long prompts
# are deliberately KEPT: multi-paragraph ambiguous prose is the keyword-blind
# regime the 9B rescue exists to serve, and it is where the eval has least
# coverage.
MIN_LEN = 30
MAX_LEN = 2000

# Injected/harness content that is not the operator speaking.
_INJECTED = re.compile(
    r"<(command-message|command-name|command-args|local-command|system-reminder|"
    r"task-notification|function_results|thinking)|^\s*\[SYSTEM|tool_use_id|"
    r"^\s*(Caveat|<user-prompt-submit-hook>)", re.I)

# A task asks for work. Require an action verb near the start, which is how the
# S5 seed harvest separated tasks from chatter.
_ACTION = re.compile(
    r"\b(add|build|fix|refactor|implement|create|write|update|change|remove|delete|"
    r"migrate|debug|investigate|diagnose|optimi[sz]e|profile|rename|move|split|"
    r"merge|wire|hook|set up|setup|configure|convert|replace|support|handle|"
    r"harden|secure|cache|test|verify|audit|review|scan|analy[sz]e|make|"
    r"resolve|repair|improve|extend|expose|render|parse|validate|instrument|"
    r"deploy|restart|roll ?back|upgrade|downgrade|install|provision|"
    r"benchmark|document|log|monitor|alert|backfill|seed|sync)\b", re.I)

# Pure conversation / process chatter, even when long enough to pass MIN_LEN.
_CHATTER = re.compile(
    r"^\s*(ok|okay|yes|no|sure|thanks|thank you|proceed|continue|go ahead|do both|"
    r"it is done|it's done|done|nice|great|perfect|got it|i see|correct|right|"
    r"merged|i merged|approved|lgtm|nvm|never ?mind)\b[\s.!,]*$", re.I)

# An APPROVAL or acknowledgement that happens to contain an action verb, e.g.
# "Proceed with merge, it works fine." or "It looks good, go ahead and merge it."
# These are replies to the agent, not requests, and they polluted the first
# harvest because "merge" reads as an action verb.
_APPROVAL_LEAD = re.compile(
    r"^\s*(proceed|go ahead|ok|okay|alright|sounds good|looks good|it looks good|"
    r"perfect|great|nice|makes sense|that works|works fine|yes|yep|sure|do it|"
    r"i merged|i've merged|ill merge|i'll merge|merged|already merged|"
    r"i approved|approved|lgtm|agreed|correct|exactly|thanks|thank you)\b", re.I)

# Instructions about how the AGENT should behave, not engineering work the
# router would dispatch ("make a note to always use your skills", "from now on
# ...", "remember to ..."). They contain action verbs but have no codebase.
_META_INSTRUCTION = re.compile(
    r"\b(make a note|from now on|going forward|for future reference|remember (to|that)|"
    r"always (use|utilize|invoke|run)|never (use|forget)|in future sessions|"
    r"keep in mind|note to self|update your (memory|instructions))\b", re.I)

_SCRUBS = (
    # order matters: specific before general
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<IP>"),
    (re.compile(r"/Users/[A-Za-z0-9_.-]+"), "~"),
    (re.compile(r"/home/[A-Za-z0-9_.-]+"), "~"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<EMAIL>"),
    (re.compile(r"\b[a-z0-9-]+@[a-z0-9-]+\b(?=:)"), "<USER>@<HOST>"),   # scp/ssh user@host
    (re.compile(r"\b(?:ssh-rsa|ssh-ed25519)\s+\S+"), "<SSH_KEY>"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|xox[baprs]-[A-Za-z0-9-]+)"), "<TOKEN>"),
)


def scrub(text: str, redact=()) -> str:
    """Remove operator-identifying and secret-shaped substrings.

    ``redact`` holds extra literal terms (usernames, hostnames, GitHub handles)
    that structural patterns cannot catch. It is a PARAMETER rather than a
    constant on purpose: writing the operator's real name and handles into this
    file would leak exactly what the function exists to remove, and this repo is
    public. Pass them via ``--redact`` at call time instead.
    """
    out = text
    for pattern, repl in _SCRUBS:
        out = pattern.sub(repl, out)
    for term in redact:
        if term:
            out = re.sub(re.escape(term), "<REDACTED>", out, flags=re.I)
    return out


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_task_shaped(text: str) -> bool:
    """True if this reads like a request to do engineering work."""
    if not text:
        return False
    stripped = text.strip()
    if not (MIN_LEN <= len(stripped) <= MAX_LEN):
        return False
    if _INJECTED.search(stripped) or _CHATTER.match(stripped):
        return False
    if _APPROVAL_LEAD.match(stripped):     # a reply to the agent, not a request
        return False
    if _META_INSTRUCTION.search(stripped):  # about the agent, not about code
        return False
    if stripped.startswith(("#", "```", "|")):     # pasted doc / table / code block
        return False
    # an action verb in the opening clause, where a real instruction puts it
    return bool(_ACTION.search(stripped[:200]))


def _content_text(message) -> str:
    """Flatten a transcript message's content to plain text."""
    content = (message or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def from_transcripts(paths) -> list:
    """Operator messages from Claude Code session transcripts."""
    found = []
    for path in paths:
        try:
            handle = Path(path).open(errors="ignore")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") != "user":
                    continue
                text = _content_text(row.get("message"))
                if text:
                    found.append(("transcript", text.strip()))
    return found


def from_telemetry(path) -> list:
    """Task texts from the route-decision ledger."""
    found = []
    try:
        handle = Path(path).open(errors="ignore")
    except OSError:
        return found
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            text = row.get("task_text") or (row.get("data") or {}).get("task_text")
            if text:
                found.append(("telemetry", str(text).strip()))
    return found


def harvest(sources, exclude_texts=(), redact=()) -> list:
    """Filter, scrub, and dedupe candidates into eval-ready rows.

    ``exclude_texts`` is the normalized training-set text; any match is dropped
    so the eval set stays disjoint from what the model was trained on.
    """
    excluded = {norm(t) for t in exclude_texts}
    seen, rows = set(), []
    for origin, raw in sources:
        if not is_task_shaped(raw):
            continue
        cleaned = scrub(" ".join(raw.split()), redact=redact)
        if not is_task_shaped(cleaned):   # scrubbing may shorten it below the band
            continue
        key = norm(cleaned)
        if key in seen or key in excluded:
            continue
        seen.add(key)
        rows.append({"task": cleaned, "origin": origin})
    return rows


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Harvest real human-written tasks for an eval set")
    p.add_argument("--transcripts", nargs="*", default=[])
    p.add_argument("--telemetry", default=str(repo / "telemetry/events.jsonl"))
    p.add_argument("--exclude", nargs="*",
                   default=[str(repo / "evals/datasets/distilled/train_v2.jsonl")],
                   help="jsonl files whose 'task' values must not appear in the output")
    p.add_argument("--out", default=str(repo / "evals/datasets/eval_candidates.jsonl"))
    p.add_argument("--redact", nargs="*", default=[],
                   help="extra literal terms to redact (usernames, hostnames, handles); "
                        "kept out of source so this public repo does not embed them")
    a = p.parse_args(argv)

    exclude = []
    for path in a.exclude:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        exclude.append(json.loads(line).get("task", ""))
        except OSError:
            pass

    sources = from_transcripts(a.transcripts) + from_telemetry(a.telemetry)
    rows = harvest(sources, exclude_texts=exclude, redact=a.redact)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    from collections import Counter
    print(json.dumps({
        "candidates_in": len(sources),
        "harvested": len(rows),
        "by_origin": dict(Counter(r["origin"] for r in rows)),
        "excluded_against": len(set(norm(t) for t in exclude)),
        "out": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
