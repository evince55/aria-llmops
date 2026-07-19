"""P1 — the specification-quality (ambiguity) probe.

The operator's adjudication of the harvested eval set exposed a one-directional
residual between the rubric and the routing decision: every rubric-vs-operator
disagreement (4/4) was an UP-tiering, and every one had the same cause — the
task was underspecified, so succeeding requires inferring unstated intent,
discovering environment state, or checking docs rather than assuming. The
rubric has no axis for that, so the tier labelers had nowhere to put it.

Hypothesis under test: the cheap cloud labelers CAN see underspecification when
asked about it directly, even though they cannot fold it into a tier — i.e. the
signal is extractable as a second question. If true, `tier + ambiguity flag`
should reconstruct the operator's labels better than tier alone, and the router
gains an ambiguity gate (underspecified -> route up one tier).

Design constraints inherited from the eval work:
- Labeler independence: only opencode-go cloud models, never a component of the
  router under test.
- The bump NEVER escalates into CRITICAL. CRITICAL is consequence-defined
  (data loss, money, security, downtime); ambiguity raises the capability a
  task needs, not its blast radius. The operator's own labels agree — no
  ambiguity up-tiering landed on CRITICAL.

Stdlib only; opencode via subprocess through judge_labels.call_judge.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.judge_labels import call_judge, _ANSI_RE  # noqa: E402
from evals.label_eval_set import EVAL_LABELERS  # noqa: E402  (3 labs, opencode-go only)

SPEC_VOCAB = ("SPECIFIED", "UNDERSPECIFIED")
DISCOVERY_VOCAB = ("DEFINED", "DISCOVERY")
_BATCH = 20
_ATTEMPTS = 2

# Ambiguity raises required capability one step; consequence tiers are immune.
BUMP = {"SIMPLE": "MODERATE", "MODERATE": "COMPLEX",
        "COMPLEX": "COMPLEX", "CRITICAL": "CRITICAL"}


def build_spec_prompt(tasks: list) -> str:
    """Ask ONLY about specification quality. Difficulty is deliberately out of
    scope — leaking the tier question back in would just re-measure the rubric."""
    lines = [
        "For each developer task below, judge ONLY how fully specified it is.",
        "Ignore how difficult the work would be.",
        "",
        "SPECIFIED = a competent developer could start immediately: the goal,",
        "scope, and success criteria are stated or obvious from the text alone.",
        "UNDERSPECIFIED = acting requires guessing unstated intent, discovering",
        "environment or system state, choosing among unstated options, doing",
        "external research just to know what to do, or the text references",
        "context it does not contain ('it', 'the site', 'option 1', 'whatever",
        "else you need').",
        "",
        "Judge the TEXT as a stranger would read it.",
        "",
        "TASKS:",
    ]
    for i, t in enumerate(tasks):
        lines.append(f"{i}. {t}")
    lines += [
        "",
        'Reply with ONLY a strict JSON array, no prose, no markdown fence: '
        '[{"i":0,"spec":"SPECIFIED"}, ...]',
        f"Return exactly {len(tasks)} objects, one per task index.",
    ]
    return "\n".join(lines)


def build_discovery_prompt(tasks: list) -> str:
    """Round 2. Round 1 measured referential completeness (deixis) and it
    turned out ubiquitous in real conversational traffic and uncorrelated with
    the operator's up-tiering. What the operator actually priced in was
    METHOD-OPENNESS: whether the agent must discover what to do before doing
    it. Missing context references are explicitly ruled OUT here."""
    lines = [
        "For each developer task below, judge ONLY whether the METHOD of",
        "solving it is defined. Ignore difficulty. Ignore missing context",
        "references ('it', 'PR 16', 'option 1') — assume the agent has the",
        "conversation and can resolve those.",
        "",
        "DEFINED = the path is stated or obvious: what to change, where, and",
        "what done looks like follow directly from the text. Executing it is",
        "carrying out a known plan, even a long one.",
        "DISCOVERY = the agent must first FIND OUT what to do: diagnose an",
        "unexplained failure, explore a system to locate the problem, research",
        "or evaluate approaches, or choose among options the text leaves open",
        "('find the best use', 'figure out what's broken', 'build whatever is",
        "needed', 'improve X' with no stated target).",
        "",
        "TASKS:",
    ]
    for i, t in enumerate(tasks):
        lines.append(f"{i}. {t}")
    lines += [
        "",
        'Reply with ONLY a strict JSON array, no prose, no markdown fence: '
        '[{"i":0,"method":"DEFINED"}, ...]',
        f"Return exactly {len(tasks)} objects, one per task index.",
    ]
    return "\n".join(lines)


def _extract_field(raw: str, n: int, field: str, vocab: tuple) -> dict:
    """{index: value} for one labeled field; longest-valid-array strategy as in
    tier extraction (TUI chrome, fences, and echoed example arrays all
    survivable)."""
    text = _ANSI_RE.sub("", raw or "")
    best: dict = {}
    for match in re.finditer(r"\[.*?\]", text, re.DOTALL):
        try:
            arr = json.loads(match.group(0))
        except (ValueError, TypeError):
            continue
        if not isinstance(arr, list):
            continue
        got = {}
        for obj in arr:
            if not isinstance(obj, dict):
                continue
            i, val = obj.get("i"), obj.get(field)
            if isinstance(i, int) and 0 <= i < n and val in vocab:
                got[i] = val
        if len(got) > len(best):
            best = got
    return best


def extract_spec(raw: str, n: int) -> dict:
    return _extract_field(raw, n, "spec", SPEC_VOCAB)


def extract_discovery(raw: str, n: int) -> dict:
    return _extract_field(raw, n, "method", DISCOVERY_VOCAB)


MODES = {
    # mode -> (prompt builder, extractor, value meaning "flag this row")
    "spec": (build_spec_prompt, extract_spec, "UNDERSPECIFIED"),
    "discovery": (build_discovery_prompt, extract_discovery, "DISCOVERY"),
}


def probe_rows(rows: list, models=EVAL_LABELERS, batch_size: int = _BATCH,
               cwd: Path = Path("."), mode: str = "spec") -> list:
    """Attach per-model votes and a majority flag to every row.

    ``mode`` picks the construct being measured (see MODES). Output rows carry
    ``spec_votes`` ({model: vote}), ``underspecified`` (True/False on a
    >=2-of-3 majority for the mode's flag value, None when fewer than two votes
    parsed — reported, never guessed), and ``probe_mode``.
    """
    for m in models:
        if not m.startswith("opencode-go/"):
            raise SystemExit(f"refusing non-opencode-go labeler: {m}")
    builder, extractor, flag_value = MODES[mode]
    tasks = [r["task"] for r in rows]
    votes: dict = {m: {} for m in models}
    for model in models:
        for start in range(0, len(tasks), batch_size):
            chunk = tasks[start:start + batch_size]
            print(f"  {model}: batch {start // batch_size} ({len(chunk)} tasks)",
                  file=sys.stderr)
            got: dict = {}
            prompt = builder(chunk)
            for attempt in range(_ATTEMPTS):
                got = extractor(call_judge(model, prompt, cwd), len(chunk))
                if len(got) == len(chunk):
                    break
                print(f"  ~ {model}: {len(got)}/{len(chunk)} parsed"
                      f"{' - retrying' if attempt + 1 < _ATTEMPTS else ''}",
                      file=sys.stderr)
            for local_i, v in got.items():
                votes[model][start + local_i] = v

    out = []
    for idx, row in enumerate(rows):
        sv = {m: votes[m].get(idx) for m in models}
        cast = [v for v in sv.values() if v is not None]
        flag = None
        if len(cast) >= 2:
            flag = Counter(cast).get(flag_value, 0) >= 2
        out.append({**row, "spec_votes": sv, "underspecified": flag,
                    "probe_mode": mode})
    return out


def composite_tier(base_tier: str, underspecified) -> str:
    """The P1 routing rule: bump one step on a confirmed ambiguity flag,
    never into CRITICAL, never on a missing flag."""
    if underspecified and base_tier in BUMP:
        return BUMP[base_tier]
    return base_tier


def reconstruction_report(rows: list) -> dict:
    """Score the hypothesis on rows that carry an operator label.

    Baseline = cloud-majority tier alone; composite = majority + bump. Also
    reports whether the flag SEPARATES the residual (fires on baseline misses,
    stays quiet on baseline agreements) — the cleanest readout even if the bump
    rule itself needs tuning later.
    """
    scored = [r for r in rows if r.get("expected_tier") and r.get("model_votes")]
    base_ok = comp_ok = 0
    flag_on_miss = miss = flag_on_agree = agree = 0
    detail = []
    for r in scored:
        op = r["expected_tier"]
        maj = Counter(r["model_votes"].values()).most_common(1)[0][0]
        comp = composite_tier(maj, r.get("underspecified"))
        base_ok += (maj == op)
        comp_ok += (comp == op)
        if maj == op:
            agree += 1
            flag_on_agree += bool(r.get("underspecified"))
        else:
            miss += 1
            flag_on_miss += bool(r.get("underspecified"))
        detail.append({"task": r["task"][:70], "operator": op, "cloud_majority": maj,
                       "underspecified": r.get("underspecified"), "composite": comp})
    return {
        "n": len(scored),
        "baseline_matches": base_ok,
        "composite_matches": comp_ok,
        "flag_fired_on_baseline_misses": f"{flag_on_miss}/{miss}",
        "flag_fired_on_baseline_agreements": f"{flag_on_agree}/{agree}",
        "rows": detail,
    }


def _read_jsonl(path: Path) -> list:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="P1: specification-quality probe")
    p.add_argument("--in-files", nargs="+", required=True)
    p.add_argument("--out-file", required=True)
    p.add_argument("--mode", choices=sorted(MODES), default="spec")
    p.add_argument("--repo", default=str(repo))
    a = p.parse_args(argv)

    rows = []
    for f in a.in_files:
        rows.extend(_read_jsonl(Path(f)))
    probed = probe_rows(rows, cwd=Path(a.repo), mode=a.mode)

    out = Path(a.out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in probed:
            fh.write(json.dumps(r) + "\n")

    flags = Counter("underspecified" if r["underspecified"] else
                    ("unknown" if r["underspecified"] is None else "specified")
                    for r in probed)
    report = reconstruction_report(probed)
    print(json.dumps({"n": len(probed), "flags": dict(flags),
                      "reconstruction": {k: v for k, v in report.items() if k != "rows"},
                      "out": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
