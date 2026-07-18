#!/usr/bin/env python3
"""Flywheel S5, stage 2 — teacher-labeled distillation set builder.

Turns real harvested seed tasks (the distinct prose prompts exported by
`telemetry.py flywheel export --enrich-tiers`) into a teacher-labeled training
set for the S5 fine-tune. For each seed the teacher (`opencode-go/minimax-m3`)
emits K faithful paraphrase VARIATIONS, each labeled with a router tier, as
strict JSON. We write one row per example to `evals/datasets/distilled/train.jsonl`:

    {"task", "tier", "source": "seed"|"synthetic", "teacher", "seed_ref", "ts"}

The original seed is emitted too (source="seed"), tiered by the teacher via the
modal tier of its faithful paraphrases (severity-tiebroken).

Non-negotiables carried from the flywheel intake work:
  * QUARANTINE — no emitted task may equal any task in
    `evals/datasets/labeled_tasks*.jsonl` (the held-out eval instrument). Seeds in
    that set are skipped; colliding variations are dropped; a final assert guards
    the whole batch. (Same idea as `telemetry/flywheel.py`.)
  * PROVENANCE — every row is tagged source=seed|synthetic and shares a seed_ref.
  * STDLIB-ONLY — no third-party imports; the teacher call is an injectable
    `complete(prompt) -> str` so tests never touch the network.

The teacher prompt below is the R4-validated template, used verbatim; it embeds
the router's exact rubric (llmops.py `_CLASSIFY_PROMPT`) so the teacher matches
the production classifier's intent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger("distill_generate")

# The router's 4 tiers (mirror of llmops._TIERS), most-severe first. Kept local
# so this module stays importable without llmops' module-level env reads.
SEVERITY_ORDER = ("CRITICAL", "COMPLEX", "MODERATE", "SIMPLE")
VALID_TIERS = frozenset(SEVERITY_ORDER)

DEFAULT_K = 4
DEFAULT_TEACHER = "opencode-go/minimax-m3"

_HERE = Path(__file__).resolve().parent          # evals/
_REPO = _HERE.parent                             # tools/llmops/
_DATASET_DIR = _HERE / "datasets"
_DEFAULT_SEEDS = _REPO / "telemetry" / "flywheel_pairs.jsonl"
_DEFAULT_OUT = _DATASET_DIR / "distilled" / "train.jsonl"

# --- FINAL VALIDATED TEACHER PROMPT (recon R4; placeholders {seed} and {k}) --
# Substituted with str.replace (NOT str.format) because the template contains
# literal JSON braces. Do not edit without re-validating against minimax-m3.
TEACHER_PROMPT = """You are a senior software engineer building training data for a task-difficulty ROUTER.

Given ONE seed developer task, produce {k} realistic PARAPHRASE VARIATIONS of it: the kind of
prompt a real developer would actually type to a coding assistant, in the SAME spirit and scope as
the seed. Vary wording, phrasing, tone, and detail (terse vs. verbose, casual vs. precise, first
person vs. imperative), but keep every variation a faithful restatement of the SAME underlying task.
Do NOT invent a different task and do NOT add or remove scope.

Then assign EACH variation exactly one TIER. Judge by BOTH effort and RISK, and escalate on
CONSEQUENCE, not vocabulary. Use this rubric verbatim (it is the router's exact rubric):

SIMPLE = typo, rename, formatting, one small function, a doc/comment, or a failing build/test.
MODERATE = a feature, component, or endpoint, or wiring across a few files.
COMPLEX = a refactor, concurrency, performance work, a subtle bug, algorithm design, or root-cause debugging.
CRITICAL = getting it wrong causes real harm: permanent data loss or corruption; mishandling money
(double-charging, refund/billing errors, leaking funds); exposing or leaking user or private data; an
auth/authorization bypass or account takeover; or taking production down. A task is CRITICAL by its
CONSEQUENCE even if it sounds like an ordinary bug or a small change (e.g. "a save race truncates the
file and users lose their data", or "downloaded files are world-readable to other apps" are CRITICAL).
But a mere mention of a sensitive DOMAIN is NOT enough: adding a payment button, renaming an
AuthManager, or editing encryption docs is NOT critical.

Tier by what the task ACTUALLY entails, not by which scary or casual words a paraphrase happens to use.
A faithful paraphrase of the same task usually keeps the same tier; only shift tier if the rephrasing
genuinely changes the consequence of getting it wrong.

Output STRICT JSON and NOTHING ELSE: a single JSON array of exactly {k} objects, each of the form
{"task": "<the variation text>", "tier": "<SIMPLE|MODERATE|COMPLEX|CRITICAL>"}. No markdown, no code
fences, no keys other than "task" and "tier", and no commentary before or after the array.

Seed task:
{seed}"""


# --------------------------------------------------------------------------- #
# seed reading
# --------------------------------------------------------------------------- #
def read_seeds(path) -> list[str]:
    """Read distinct seed task texts, preserving first-seen order.

    Accepts a JSONL file (each line an object with a `task_text` — the flywheel
    export shape — or a `task` key) or a plaintext file (one seed per line).
    Blank lines are ignored; duplicates are collapsed.
    """
    seeds: list[str] = []
    seen: set[str] = set()
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        text = None
        parsed_json = False
        if line[0] in "{[":
            try:
                obj = json.loads(line)
                parsed_json = True
            except Exception:
                parsed_json = False
            if parsed_json and isinstance(obj, dict):
                text = obj.get("task_text") or obj.get("task")
        if text is None and not parsed_json:
            text = line          # a bare plaintext seed line
        if not text:
            continue
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            seeds.append(text)
    return seeds


# --------------------------------------------------------------------------- #
# teacher prompt + reply parsing
# --------------------------------------------------------------------------- #
def build_prompt(seed: str, k: int) -> str:
    """Fill the validated template. .replace (not .format) — the template holds
    literal JSON braces; {k} is filled first so a seed containing braces is safe."""
    return TEACHER_PROMPT.replace("{k}", str(k)).replace("{seed}", seed)


def extract_json_array(stdout: str):
    """Return the first balanced top-level JSON array in `stdout`.

    opencode prints TUI header/footer lines around the model reply; the reply is
    the JSON array. Brackets inside JSON strings are ignored. Raises ValueError
    if no balanced array is present."""
    start = stdout.find("[")
    if start == -1:
        raise ValueError("no '[' in output")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(stdout)):
        c = stdout[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return json.loads(stdout[start:i + 1])
    raise ValueError("unbalanced JSON array")


def parse_teacher_reply(stdout: str, k: int | None = None) -> list[dict]:
    """Parse the teacher's reply into a list of valid {task, tier} dicts.

    Robust by contract: malformed output (no array, non-list, bad items) is
    skipped and logged, never raised. Items are kept only if `task` is a
    non-empty string and `tier` is one of the four router tiers (case-normalized)."""
    try:
        arr = extract_json_array(stdout)
    except Exception as exc:
        LOG.warning("teacher reply unparseable (%s); skipping", exc)
        return []
    if not isinstance(arr, list):
        LOG.warning("teacher reply top-level is %s, not a list; skipping",
                    type(arr).__name__)
        return []
    items: list[dict] = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        task = it.get("task")
        tier = it.get("tier")
        if not isinstance(task, str) or not task.strip():
            continue
        if not isinstance(tier, str):
            continue
        tier_up = tier.strip().upper()
        if tier_up not in VALID_TIERS:
            continue
        items.append({"task": task.strip(), "tier": tier_up})
    if k is not None and len(items) != k:
        LOG.info("teacher returned %d valid item(s) (asked for %d)", len(items), k)
    return items


# --------------------------------------------------------------------------- #
# tier aggregation + provenance
# --------------------------------------------------------------------------- #
def seed_tier(tiers) -> str:
    """The teacher's tier for the seed itself = the modal tier of its faithful
    paraphrases, ties broken toward the most severe (the router's escalation
    convention). `tiers` must be non-empty and all valid."""
    counts = Counter(tiers)
    top = max(counts.values())
    for tier in SEVERITY_ORDER:            # most severe first
        if counts.get(tier, 0) == top:
            return tier
    # unreachable if tiers are valid; defensive fallback
    return counts.most_common(1)[0][0]


def seed_ref(seed: str) -> str:
    """Stable short id tying a seed's rows (seed + its variations) together."""
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# quarantine set
# --------------------------------------------------------------------------- #
def eval_task_texts() -> set[str]:
    """The held-out eval instrument: every `task` in evals/datasets/labeled_tasks*.jsonl.
    These are NEVER training inputs (mirrors telemetry/flywheel._eval_task_texts)."""
    texts: set[str] = set()
    for p in sorted(_DATASET_DIR.glob("labeled_tasks*.jsonl")):
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    texts.add(json.loads(line)["task"])
    return texts


# --------------------------------------------------------------------------- #
# generation
# --------------------------------------------------------------------------- #
def generate_examples(seeds, complete, *, k: int = DEFAULT_K,
                      teacher: str = DEFAULT_TEACHER, eval_texts=None,
                      now=None, logger=None) -> list[dict]:
    """Drive the (injected) teacher over `seeds` and return distillation examples.

    `complete`: callable(prompt: str) -> str — the teacher. Injectable so tests
    never hit the network. `eval_texts`: the quarantine set (loaded from the
    labeled eval files if None). `now`: injectable ISO-timestamp source.
    """
    if eval_texts is None:
        eval_texts = eval_task_texts()
    if now is None:
        now = lambda: datetime.now(timezone.utc).isoformat()
    log = logger or LOG

    examples: list[dict] = []
    seen: set[str] = set()            # global dedup of emitted task text

    def _emit(task, tier, source, ref, ts):
        if task in seen:
            return
        if task in eval_texts:
            log.warning("quarantine: dropping %s task colliding with eval set: %.60s",
                        source, task)
            return
        seen.add(task)
        examples.append({"task": task, "tier": tier, "source": source,
                         "teacher": teacher, "seed_ref": ref, "ts": ts})

    for seed in seeds:
        if seed in eval_texts:
            log.warning("quarantine: skipping seed found in eval set: %.60s", seed)
            continue
        raw = complete(build_prompt(seed, k))
        items = parse_teacher_reply(raw, k=k)
        if not items:
            log.warning("no valid teacher items for seed; skipping: %.60s", seed)
            continue
        ref = seed_ref(seed)
        ts = now()
        _emit(seed, seed_tier([it["tier"] for it in items]), "seed", ref, ts)
        for it in items:
            _emit(it["task"], it["tier"], "synthetic", ref, ts)

    emitted = {e["task"] for e in examples}
    assert emitted.isdisjoint(eval_texts), \
        "QUARANTINE VIOLATION: emitted task(s) overlap the held-out eval set"
    return examples


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #
def write_jsonl(examples, path) -> int:
    """Write examples one-per-line to `path` (creating parents). Returns count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for e in examples:
            fh.write(json.dumps(e) + "\n")
    return len(examples)


# --------------------------------------------------------------------------- #
# real teacher (subprocess) + CLI
# --------------------------------------------------------------------------- #
def make_teacher(model: str = DEFAULT_TEACHER, timeout: int = 300):
    """A real `complete(prompt) -> str` driving minimax-m3 via the opencode CLI.
    (Never invoked by tests — they inject a fake.)"""
    def complete(prompt: str) -> str:
        proc = subprocess.run(
            ["opencode", "run", "-m", model, prompt],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.stdout
    return complete


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Flywheel S5 stage 2: teacher-labeled distillation set builder.")
    ap.add_argument("--seeds", default=str(_DEFAULT_SEEDS),
                    help="seed source: JSONL (task_text/task) or plaintext lines "
                         "(default: telemetry/flywheel_pairs.jsonl)")
    ap.add_argument("--out", default=str(_DEFAULT_OUT),
                    help="output train.jsonl (default: evals/datasets/distilled/train.jsonl)")
    ap.add_argument("-k", "--variations", type=int, default=DEFAULT_K,
                    help="paraphrase variations per seed (default: %(default)s)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap number of seeds processed (for smoke runs)")
    ap.add_argument("--teacher-model", default=DEFAULT_TEACHER,
                    help="opencode model id for the teacher (default: %(default)s)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    seeds = read_seeds(args.seeds)
    if args.limit is not None:
        seeds = seeds[:args.limit]
    if not seeds:
        LOG.error("no seeds read from %s", args.seeds)
        return 1

    LOG.info("read %d seed(s) from %s; K=%d; teacher=%s",
             len(seeds), args.seeds, args.variations, args.teacher_model)
    complete = make_teacher(args.teacher_model)
    examples = generate_examples(seeds, complete, k=args.variations,
                                 teacher=args.teacher_model)
    n = write_jsonl(examples, args.out)

    summary = {
        "seeds_in": len(seeds),
        "examples_out": n,
        "by_source": {s: sum(1 for e in examples if e["source"] == s)
                      for s in sorted({e["source"] for e in examples})},
        "by_tier": {t: sum(1 for e in examples if e["tier"] == t)
                    for t in sorted({e["tier"] for e in examples})},
        "teacher": args.teacher_model,
        "out": str(args.out),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
