"""Regenerate the COMPLEX training slice — the fix S6's audit specified.

The promotion gate (2026-07-20) failed on one tier: the tuned E2B sends 11 of
38 COMPLEX eval rows to MODERATE, costing 18 points of recall and blocking
promotion outright. The cause was predicted two rounds earlier and never fixed.

S6's audit (`2026-07-18-s6-dataset-scale-results.md`) found the COMPLEX
generator emitting **prescribed-fix tickets**: tasks that name BOTH the cause
and the remedy ("N+1 -> switch to eager loading with selectinload"). Those are
MODERATE wiring dressed in concurrency vocabulary, and the judges said so —
only 66 of 160 generated COMPLEX rows survived as COMPLEX. The model was then
trained on a COMPLEX slice that largely looks like MODERATE work, and it
learned exactly that.

The audit's prescription, applied here: **withhold the diagnosis and the
remedy.** What separates COMPLEX from MODERATE is not subject matter but
whether unsolved diagnostic work remains. Tasks that preserve genuine
uncertainty hold COMPLEX; tasks that hand over the answer collapse to MODERATE.

Training data stays SYNTHETIC on purpose. The eval instrument is now
human-written GitHub text; training on that same source would recreate the
distribution-match inflation that made a 35b-generated eval read 0.92 against
0.738 on human text. Independent sources keep the gate honest.

Over-generation is budgeted from S6's measured yield (66/160 = 41%), so ~2.4x
the target is generated and the judges discard the rest.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.distill_generate import make_teacher  # noqa: E402

# The contrastive examples are lifted verbatim from S6's audit — they are the
# actual rows that collapsed, and the actual rows that held.
COMPLEX_PROMPT = """You are a senior software engineer building training data for a task-difficulty ROUTER.

Write {k} realistic developer tasks that are genuinely COMPLEX, in the domain: {domain}.

COMPLEX means: a refactor, concurrency work, performance work, a subtle bug, algorithm design, or
root-cause debugging.

THE ONE RULE THAT MATTERS — WITHHOLD THE DIAGNOSIS AND THE FIX.

What separates COMPLEX from MODERATE is not the subject matter, it is whether UNSOLVED DIAGNOSTIC
WORK REMAINS. A task that hands over the root cause and the remedy is ordinary wiring work wearing
concurrency vocabulary, and it is MODERATE no matter how technical it sounds. Write tasks where the
developer reports a SYMPTOM and genuine uncertainty, and the assistant still has to find the cause.

WRONG (these are MODERATE — they name the cause AND the fix):
  - "The /api/radio endpoint has an N+1 query problem; switch to eager loading with selectinload."
  - "This handler is declared async def but makes three sequential blocking requests.get calls,
     convert them to a shared httpx.AsyncClient."
  - "Two clients race to write the same file; add per-video-id coalescing with an asyncio lock."

RIGHT (these are COMPLEX — a symptom plus real uncertainty):
  - "Requests to /api/radio get dramatically slower as the library grows and I can't work out why.
     Profile it and fix whatever is actually causing it."
  - "Our test suite is flaky in CI but green locally. I'm fairly sure something is leaking state
     between tests but I haven't found it. Track it down."
  - "Memory climbs steadily over a few hours until the pod is OOM-killed. I suspect something isn't
     being released on an error path, but I haven't confirmed it. Root-cause it."

More ways to keep real uncertainty: "intermittent, can't reproduce reliably"; "worked before the
upgrade, no idea what changed"; "the profiler points at X but that doesn't explain Y"; "happens
only under load"; asking WHY as well as asking for a fix.

Also required:
- Concrete and specific to {domain} — name plausible files, APIs, frameworks.
- Vary phrasing, length (terse vs verbose), and register (casual vs precise). No near-duplicates.
- Do NOT make them sound urgent or dangerous to inflate them: CRITICAL is about consequence
  (data loss, money, auth bypass, production down). These must be COMPLEX by DIFFICULTY, not harm.

Output STRICT JSON and NOTHING ELSE: a single JSON array of exactly {k} objects, each
{"task": "<the task text>"}. No markdown, no code fences, no other keys, no commentary."""

DOMAINS = (
    "iOS/Swift app",
    "Python/FastAPI backend",
    "web frontend (JS/CSS)",
    "infra/devops/k8s/CI",
)

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def extract_tasks(raw: str) -> list:
    """Longest-valid-array extraction — opencode wraps replies in TUI chrome and
    models sometimes fence their JSON or echo the prompt's example array."""
    text = _ANSI.sub("", raw or "")
    best: list = []
    for m in re.finditer(r"\[.*?\]", text, re.DOTALL):
        try:
            arr = json.loads(m.group(0))
        except (ValueError, TypeError):
            continue
        if not isinstance(arr, list):
            continue
        got = [o["task"].strip() for o in arr
               if isinstance(o, dict) and isinstance(o.get("task"), str) and o["task"].strip()]
        if len(got) > len(best):
            best = got
    return best


# A generated task that still hands over the remedy defeats the whole exercise.
# Cheap deterministic guard; the cross-model judges remain the real arbiter.
_PRESCRIBES_FIX = re.compile(
    # `[\w-]+` not `\w+`: the audit's own example is "add a per-video-id asyncio
    # lock", and \w+ stops at the hyphen, so the canonical case slipped through.
    r"\b(?:switch to|convert (?:them|it|these) to|replace (?:it|them) with|"
    r"add (?:a |an )?(?:[\w-]+ ){0,3}lock|use (?:a |an )?(?:shared |single )?\w+Client|"
    r"wrap (?:it|them) in|change (?:it|them) to|refactor (?:it|them) to use|"
    r"just (?:add|use|set)|the fix is|should be changed to)\b", re.I)


def prescribes_fix(task: str) -> bool:
    return bool(_PRESCRIBES_FIX.search(task or ""))


def generate(complete, per_domain: int, k: int = 15) -> list:
    rows, seen = [], set()
    for domain in DOMAINS:
        need = per_domain
        rounds = 0
        while need > 0 and rounds < 8:
            rounds += 1
            prompt = COMPLEX_PROMPT.replace("{k}", str(k)).replace("{domain}", domain)
            try:
                raw = complete(prompt)
            except Exception as exc:  # a bad round must not lose the run
                print(f"  ! {domain} round {rounds}: {exc}", file=sys.stderr)
                continue
            got = extract_tasks(raw)
            kept = 0
            for t in got:
                key = re.sub(r"\s+", " ", t.lower())[:110]
                if key in seen or prescribes_fix(t) or not (60 <= len(t) <= 600):
                    continue
                seen.add(key)
                rows.append({"task": t, "tier": "COMPLEX", "domain": domain,
                             "source": "synthetic-v3-complex"})
                kept += 1
                need -= 1
                if need <= 0:
                    break
            print(f"  {domain:26s} round {rounds}: +{kept:2d} (remaining {max(need,0)})",
                  file=sys.stderr)
    return rows


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Regenerate the COMPLEX training slice")
    # 2.4x over-generation: S6 measured a 41% COMPLEX survival rate through judging.
    p.add_argument("--per-domain", type=int, default=90)
    p.add_argument("--k", type=int, default=15)
    p.add_argument("--out", default=str(repo / "evals/datasets/distilled/complex_v3_raw.jsonl"))
    a = p.parse_args(argv)

    complete = make_teacher()
    rows = generate(complete, per_domain=a.per_domain, k=a.k)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    from collections import Counter
    print(json.dumps({"generated": len(rows),
                      "by_domain": dict(Counter(r["domain"] for r in rows)),
                      "out": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
