"""Harvest HUMAN-WRITTEN, task-shaped prompts from public GitHub issues.

Why this source. The router's eval sets have a distribution problem that two
prior rounds pinned down precisely:

  * Model-generated evals inflate a model trained on model text — the tuned E2B
    scored 0.92 on `labeled_tasks_balanced.jsonl` (`source: 35b-gen`) versus
    0.738 on the human-written union. Same quarantine, opposite verdict. So the
    text has to be written by humans.
  * Natural operator usage cannot be tier-balanced. Harvesting 5,426 real
    messages yielded COMPLEX 2 / CRITICAL 1 — an operator does not spend the
    day filing auth-bypass tickets, so rare-by-nature tiers stay rare no matter
    how much traffic accrues.

GitHub issues resolve both: real developers writing real requests, spanning
typos through RCE, in the imperative/problem-report register the router
actually sees.

METHODOLOGICAL RULE — the search query is a SAMPLING strategy, never a label.
Querying "XSS" oversamples CRITICAL-ish material so the rare tiers are covered
at all, but the tier assigned to a row comes only from the independent
labelers in `label_eval_set.py` (three different opencode-go labs, none of
which is a component of the router under test). A query bucket that yields a
SIMPLE row is a correct outcome, not a bug; `query_bucket` is retained purely
so sampling bias can be audited after the fact.

Output feeds `label_eval_set.py --in-file` unchanged.

Stdlib + `gh` CLI only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Each bucket oversamples a tier's *subject matter*. Labels come from the
# labelers, not from these queries — see the module docstring.
QUERY_BUCKETS: dict[str, tuple[str, ...]] = {
    "simple-ish": (
        "typo in:title is:issue",
        "rename variable in:title is:issue",
        "update docs in:title is:issue",
        "fix broken link in:title is:issue",
    ),
    "moderate-ish": (
        "add endpoint in:title is:issue",
        "implement feature in:title is:issue",
        "add support for in:title is:issue",
        "add validation in:title is:issue",
    ),
    "complex-ish": (
        "race condition in:title is:issue",
        "memory leak in:title is:issue",
        "performance regression in:title is:issue",
        "flaky test in:title is:issue",
        "deadlock in:title is:issue",
    ),
    # Query the vulnerability CLASS, not the act of disclosure: "reporting a
    # security vulnerability" issues are notices ("Hello, I'm a security
    # engineer at...") with no work in them, and the validity gate rightly
    # rejects them — which starved the scarcest tier.
    "critical-ish": (
        "XSS in:title is:issue",
        "CSRF in:title is:issue",
        "SQL injection in:title is:issue",
        "path traversal in:title is:issue",
        "remote code execution in:title is:issue",
        "privilege escalation in:title is:issue",
        "hardcoded credentials in:title is:issue",
        "API key exposed in:title is:issue",
        "authentication bypass in:title is:issue",
        "data corruption in:title is:issue",
        "data loss in:title is:issue",
        "deletes user data in:title is:issue",
    ),
}

# Issue titles that are questions/discussion rather than work requests. The
# model validity gate in label_eval_set.py catches the rest; this is the cheap
# deterministic pre-filter (the harvest round measured ~46% non-tasks getting
# through a regex-only filter, so the gate stays load-bearing).
_NON_TASK = re.compile(
    r"^\s*(?:how (?:do|to|can)|what(?:'s| is)|why (?:do|does|is)|question\b|"
    r"discussion\b|rfc\b|poll\b|announce|release \d|v\d+\.\d+)", re.I)

_MIN_LEN, _MAX_LEN = 40, 600

# The router is prompted in English; harvested issues are global (French and
# Chinese rows came through the first pass). ASCII ratio alone is too weak
# (accented French is ~95% ASCII), so also require English function words.
_EN_HINT = re.compile(r"\b(?:the|is|are|was|were|when|should|with|this|that|"
                      r"from|not|and|for|but|have|has|does|doesn|can|will)\b", re.I)


def looks_english(text: str) -> bool:
    if not text:
        return False
    ascii_ratio = sum(ch.isascii() for ch in text) / len(text)
    return ascii_ratio >= 0.92 and bool(_EN_HINT.search(text))

# Scratch/demo/CTF repos produce issue text that looks task-shaped but describes
# nothing real ("Race Condition Test. Testing concurrent mutations" from a
# pentest sandbox). Cheap name-based drop; the 3-model validity gate downstream
# still does the heavy lifting.
_JUNK_REPO = re.compile(
    r"(?:^|[-_/])(?:test|tests|testing|demo|example|examples|sample|samples|"
    r"playground|sandbox|scratch|practice|tutorial|learning|dummy|foo|bar|"
    r"pentest|ctf|hackthebox|vuln(?:erable)?-?app|juice-?shop)(?:$|[-_/])", re.I)


def is_junk_repo(name_with_owner: str) -> bool:
    return bool(_JUNK_REPO.search(name_with_owner or ""))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def is_task_shaped(title: str, body: str) -> bool:
    """Cheap pre-filter. Deliberately permissive — the 3-model validity gate
    downstream is the real arbiter; this only drops obvious non-work."""
    t = _norm(title)
    if not t or _NON_TASK.match(t):
        return False
    combined = _norm(f"{t}. {body}")
    return _MIN_LEN <= len(combined) <= _MAX_LEN


def to_task(title: str, body: str) -> str:
    """Issue -> a prompt in the register the router sees.

    Kept as the issue's own words (title + first substantive body sentence).
    We deliberately do NOT paraphrase with a model: rewriting would reintroduce
    model-authored text, which is the exact bias this dataset exists to avoid.
    """
    t = _norm(title).rstrip(".")
    b = _norm(body)
    # strip markdown noise / templates that carry no task content
    b = re.sub(r"```.*?```", " ", b, flags=re.S)
    b = re.sub(r"<!--.*?-->", " ", b, flags=re.S)
    b = re.sub(r"#{1,6}\s*", "", b)
    b = re.sub(r"\s+", " ", b).strip()
    first = ""
    for sent in re.split(r"(?<=[.!?])\s+", b):
        if len(sent) > 25 and not sent.lower().startswith(("steps to", "expected", "actual")):
            first = sent
            break
    task = f"{t}. {first}".strip() if first else t
    return task[:_MAX_LEN].strip()


def gh_search(query: str, limit: int) -> list:
    """One `gh search issues` call. Returns [] on failure rather than aborting
    the harvest — a single bad query must not lose the whole run."""
    cmd = ["gh", "search", "issues", query, "--limit", str(limit),
           "--json", "title,body,url,repository"]
    try:
        # gh needs a login shell: the token lives in ~/.zprofile
        proc = subprocess.run(["zsh", "-lc", " ".join(
            [c if " " not in c else f"'{c}'" for c in cmd])],
            capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            print(f"  ! query failed: {query[:40]} :: {proc.stderr.strip()[:90]}", file=sys.stderr)
            return []
        return json.loads(proc.stdout or "[]")
    except (subprocess.TimeoutExpired, ValueError) as exc:
        print(f"  ! query error: {query[:40]} :: {exc}", file=sys.stderr)
        return []


def harvest(per_query: int = 40, buckets=None) -> list:
    seen: set = set()
    rows: list = []
    for bucket, queries in (buckets or QUERY_BUCKETS).items():
        for q in queries:
            hits = gh_search(q, per_query)
            kept = 0
            for h in hits:
                title, body = h.get("title", ""), h.get("body", "") or ""
                repo_name = (h.get("repository") or {}).get("nameWithOwner", "")
                if is_junk_repo(repo_name) or not is_task_shaped(title, body):
                    continue
                task = to_task(title, body)
                # Length + language are checked on the FINAL task text: the
                # pre-filter measured title+body, so a junk body could yield a
                # 9-char task ("Typo") and still pass.
                if not (_MIN_LEN <= len(task) <= _MAX_LEN) or not looks_english(task):
                    continue
                key = _norm(task).lower()[:120]
                if key in seen:
                    continue
                seen.add(key)
                repo = repo_name
                rows.append({"task": task, "origin": "github-issue",
                             "query_bucket": bucket, "repo": repo,
                             "url": h.get("url", "")})
                kept += 1
            print(f"  {bucket:14s} {q[:38]:40s} -> {kept:3d} kept", file=sys.stderr)
    return rows


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="Harvest human-written task prompts from GitHub issues")
    p.add_argument("--out", default=str(repo / "evals/datasets/gh_candidates.jsonl"))
    p.add_argument("--per-query", type=int, default=40)
    a = p.parse_args(argv)

    rows = harvest(per_query=a.per_query)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    from collections import Counter
    print(json.dumps({
        "harvested": len(rows),
        "by_query_bucket": dict(Counter(r["query_bucket"] for r in rows)),
        "distinct_repos": len({r["repo"] for r in rows}),
        "out": str(out),
        "note": "query_bucket is a SAMPLING label only; tiers come from label_eval_set.py",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
