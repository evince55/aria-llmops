"""Cross-model label verification for the S6 scaled distillation set.

Each generated task carries the tier its *generator* intended. That intent is a
weak label: the generator was told to write tier-X tasks, so it has every
incentive to believe it succeeded. This module obtains two INDEPENDENT tier
labels from two different opencode-go model families and keeps a task only when
those two judges agree, using the agreed tier as the final label (which may
differ from the generator's intent -- we trust the judges).

Why two *non-generating* judges rather than a self-check: a model grading its
own output shares its own blind spots. Two families disagreeing is real signal;
a model agreeing with itself is not.

The interesting output is not the keep/drop count, it is
``intent_confusion`` -- how the generator's intent maps onto the agreed label.
A tier that systematically collapses into a neighbour (e.g. CRITICAL judged
COMPLEX) re-starves that class, which is the exact failure this dataset exists
to fix, so it must be visible rather than silently absorbed.

Stdlib only; opencode is invoked as a subprocess.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmops import _TIERS  # noqa: E402  (shared tier vocabulary)

# Two different families on purpose -- see module docstring. Both are
# opencode-go routes; zen models are never used here.
DEFAULT_JUDGES = ("opencode-go/minimax-m3", "opencode-go/deepseek-v4-pro")

RUBRIC = """SIMPLE = typo, rename, formatting, one small function, a doc/comment, or a failing build/test.
MODERATE = a feature, component, or endpoint, or wiring across a few files.
COMPLEX = a refactor, concurrency, performance work, a subtle bug, algorithm design, or root-cause debugging.
CRITICAL = getting it wrong causes real harm: permanent data loss or corruption, money/payment mishandling, a security/auth/data-exposure hole, or production downtime.
Judge by BOTH effort AND risk; escalate on CONSEQUENCE, not vocabulary. Exactly one tier per task."""

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_BATCH = 20
_TIMEOUT_S = 300


def build_prompt(tasks: list) -> str:
    """Batch classification prompt. Indices are explicit so a reply that drops
    or reorders entries can be detected rather than silently misaligned."""
    lines = [
        "Classify each developer task into exactly one tier.",
        "",
        "RUBRIC:",
        RUBRIC,
        "",
        "TASKS:",
    ]
    for i, t in enumerate(tasks):
        lines.append(f"{i}. {t}")
    lines += [
        "",
        'Reply with ONLY a strict JSON array, no prose, no markdown fence: '
        '[{"i":0,"tier":"SIMPLE"}, ...]',
        f"Return exactly {len(tasks)} objects, one per task index.",
    ]
    return "\n".join(lines)


def extract_labels(raw: str, n: int) -> dict:
    """Pull ``{index: tier}`` out of an opencode reply.

    opencode wraps model text in TUI chrome (ANSI colour, a '> build - model'
    banner), and models sometimes fence their JSON, so we strip ANSI and scan
    every bracketed span for the last one that parses into valid tier objects.
    Returns {} when nothing usable is found -- callers retry, then drop.
    """
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
            i, tier = obj.get("i"), obj.get("tier")
            if isinstance(i, int) and 0 <= i < n and tier in _TIERS:
                got[i] = tier
        if len(got) > len(best):
            best = got
    return best


def call_judge(model: str, prompt: str, cwd: Path) -> str:
    """One opencode completion. Non-zero exit or timeout yields '' so the batch
    is retried rather than crashing the shard."""
    try:
        proc = subprocess.run(
            ["opencode", "run", "-m", model, prompt],
            cwd=str(cwd), capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        return (proc.stdout or "") + "\n" + (proc.stderr or "")
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"  ! {model} call failed: {exc}", file=sys.stderr)
        return ""


def label_batch(model: str, tasks: list, cwd: Path, attempts: int = 2) -> dict:
    """Label one batch, retrying a non-parsing or short reply once."""
    prompt = build_prompt(tasks)
    got: dict = {}
    for attempt in range(attempts):
        got = extract_labels(call_judge(model, prompt, cwd), len(tasks))
        if len(got) == len(tasks):
            return got
        print(f"  ~ {model}: got {len(got)}/{len(tasks)} labels"
              f"{' - retrying' if attempt + 1 < attempts else ' - keeping partial'}",
              file=sys.stderr)
    return got


def judge_rows(rows: list, models=DEFAULT_JUDGES, batch_size: int = _BATCH,
               cwd: Path = Path(".")) -> dict:
    """Label every row with each judge; keep rows where all judges agree.

    Returns kept rows plus the diagnostics that matter: pairwise agreement and
    the generator-intent -> agreed-label confusion matrix.
    """
    tasks = [r["task"] for r in rows]
    per_model: dict = {}
    for model in models:
        labels: dict = {}
        for start in range(0, len(tasks), batch_size):
            chunk = tasks[start:start + batch_size]
            print(f"  {model}: batch {start // batch_size} ({len(chunk)} tasks)", file=sys.stderr)
            for local_i, tier in label_batch(model, chunk, cwd).items():
                labels[start + local_i] = tier
        per_model[model] = labels

    kept, dropped, unlabeled = [], [], 0
    confusion = defaultdict(Counter)
    for idx, row in enumerate(rows):
        votes = [per_model[m].get(idx) for m in models]
        if any(v is None for v in votes):
            unlabeled += 1
            continue
        if len(set(votes)) == 1:
            agreed = votes[0]
            confusion[row.get("tier", "?")][agreed] += 1
            kept.append({
                "task": row["task"],
                "tier": agreed,
                "source": "synthetic-v2",
                "domain": row.get("domain", ""),
                "intent_tier": row.get("tier", ""),
                "judges": "+".join(m.split("/")[-1] for m in models),
            })
        else:
            dropped.append({"task": row["task"], "votes": dict(zip(models, votes))})

    decided = len(kept) + len(dropped)
    return {
        "n_in": len(rows),
        "kept": kept,
        "dropped": dropped,
        "unlabeled": unlabeled,
        "agreement_rate": round(len(kept) / decided, 4) if decided else 0.0,
        "kept_by_tier": dict(Counter(k["tier"] for k in kept)),
        "intent_confusion": {k: dict(v) for k, v in confusion.items()},
        "relabeled": sum(1 for k in kept if k["tier"] != k["intent_tier"]),
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
    p = argparse.ArgumentParser(description="Cross-model tier-label verification")
    p.add_argument("--in-file", required=True, help="raw jsonl of {task,tier,domain}")
    p.add_argument("--out-file", required=True, help="where to write agreed rows")
    p.add_argument("--report", default="", help="optional json report path")
    p.add_argument("--models", default=",".join(DEFAULT_JUDGES))
    p.add_argument("--batch-size", type=int, default=_BATCH)
    p.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    a = p.parse_args(argv)

    models = tuple(m.strip() for m in a.models.split(",") if m.strip())
    for m in models:
        if not m.startswith("opencode-go/"):
            raise SystemExit(f"refusing non-opencode-go judge: {m}")

    rows = _read_jsonl(Path(a.in_file))
    result = judge_rows(rows, models=models, batch_size=a.batch_size, cwd=Path(a.repo))

    out = Path(a.out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in result["kept"]:
            fh.write(json.dumps(row) + "\n")

    summary = {k: v for k, v in result.items() if k not in ("kept", "dropped")}
    summary.update({"in_file": a.in_file, "out_file": str(out), "n_kept": len(result["kept"])})
    if a.report:
        rp = Path(a.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps({**summary, "dropped": result["dropped"]}, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
