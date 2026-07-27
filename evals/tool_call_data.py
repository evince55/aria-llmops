"""S10 training data — self-supervised tool-call examples.

The baseline decomposition says what has to be learned. On 20 held-out tasks the
E2B base scored parse 0.90 / tool 0.90 / **strict 0.70**: it picks the right tool
almost always and loses on ARGUMENT PRECISION. So the training set is built to
teach exactly the four things those arguments require:

  * pulling a path out of varied phrasing        ("what's in X", "open X", "read X")
  * inferring a glob from a file description     ("python files" -> "*.py")
  * inferring a bool from adverbs                ("quietly" -> False, "verbosely" -> True)
  * extracting quoted content verbatim           ("save 'ok' to X" -> content="ok")

NO TEACHER. Ground truth is constructed with the example, so it is correct by
definition rather than by a cloud model's opinion. That is why this round costs
nothing, and it also sidesteps the finding that a teacher's blind spots
propagate into the student (S6: two judges wrong together on all 495 rows).

QUARANTINE IS ENFORCED IN CODE, not by intention. `generate()` refuses to emit a
task that appears in the held-out set, and a test asserts zero overlap. This
project has been bitten twice by contaminated instruments; a third would be
carelessness.
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.tool_calls import TASKS as HELD_OUT, build_prompt  # noqa: E402

# ---------------------------------------------------------------- vocabulary
# COMPOSITIONAL, NOT LITERAL. The flat lists this replaces saturated at 1,540
# unique examples — ask the generator for 20,000 and it returned 1,540 — so the
# paper's 10k-100k range was not merely untested here, it was unreachable.
#
# Composing paths from parts multiplies the space instead of enumerating it.
# Note what this does and does NOT scale: it grows the number of ROWS without
# growing the number of SENTENCE SHAPES, because the templates below are held
# fixed on purpose (adding templates would risk the phrasing leak of finding 13
# and would change two variables at once). A data curve built this way answers
# "more rows of the same shapes", and the pre-registration says so.
#
# EVERY value is filtered against all three eval sets at import, so a widened
# vocabulary cannot quietly start answering held-out tasks from memory.
_DIRS = ("lib", "app", "core", "utils", "handlers", "models", "services", "jobs",
         "config", "docs", "scripts", "web", "data", "tests", "api", "store",
         "queue", "render", "parser", "codec", "net", "fs", "math", "text", "time")
_STEMS = ("parser", "routes", "engine", "dates", "webhook", "user", "mailer", "cleanup",
          "schema", "guide", "seed", "styles", "client", "server", "worker", "adapter",
          "buffer", "cursor", "digest", "encoder", "filter", "gateway", "handler", "index",
          "loader", "mapper", "nonce", "parcel", "queue", "reducer")
_EXTS = ("py", "md", "yaml", "js", "sh", "css", "sql", "txt", "cfg", "ini")
_PATTERN_HEADS = ("FIXME", "XXX", "TODO_SOON", "NOTE", "WARN", "DEBUG", "STUB", "LEGACY",
                  "audit", "cache", "retry", "token", "secret", "socket", "buffer", "cursor",
                  "import os", "async def", "raise ValueError", "console.log")
_PATTERN_TAILS = ("", "_id", "_key", "_len", "_max", "_ttl", "_flag", "_path")
# run_tests is the tightest space (one template family, one argument that varies
# freely), so its vocabulary needs the most headroom: at n=10,000 the generator
# wants 2,500 rows per tool, and 20x12 heads/tails would have capped it at 2,400
# and silently produced an unbalanced set.
_TARGET_HEADS = ("payments", "search", "indexer", "scheduler", "notifications", "uploads",
                 "reports", "sessions", "webhooks", "migrations", "billing2", "routing",
                 "storage", "transcode", "matching", "pricing2", "shipping", "identity",
                 "catalog", "settlement", "ingest", "egress", "rollup", "compaction",
                 "failover", "throttling", "provisioning", "reconcile", "sharding", "eviction")
_TARGET_TAILS = ("", "_api", "_core", "_edge", "_batch", "_live", "_v2", "_smoke",
                 "_slow", "_unit", "_e2e", "_regress", "_nightly", "_canary", "_perf",
                 "_soak")
_CONTENT_HEADS = ("done", "pending", "ok", "draft", "ready", "skipped", "queued", "stale",
                  "warm", "cold", "primary", "backup")
_CONTENT_TAILS = ("", " v1", " v2", " v4", " alpha", " beta", " final", " retry", " hold",
                  " 7", " 11", " 23")


def _eval_values():
    """Every argument value appearing in ANY eval set.

    Filtered out of the training vocabulary at import. Quarantine has now failed
    this project twice in ways a check would have caught (findings 13, and the
    fuzzy-overlap round before it), and a vocabulary large enough to be useful is
    also large enough to collide by accident.
    """
    from evals.tool_calls import FRESH_TASKS, HARD_TASKS, REGRESSION_TASKS, TASKS
    out = set()
    for rows in (TASKS, HARD_TASKS, FRESH_TASKS, REGRESSION_TASKS):
        for t in rows:
            out |= {v for v in t["args"].values() if isinstance(v, str)}
    return out


def _build():
    banned = _eval_values()
    paths = tuple(p for p in
                  (f"{d}/{s}.{e}" for d in _DIRS for s in _STEMS for e in _EXTS)
                  if p not in banned)
    patterns = tuple(p for p in
                     (h + t for h in _PATTERN_HEADS for t in _PATTERN_TAILS)
                     if p not in banned)
    targets = tuple(t for t in
                    (h + s for h in _TARGET_HEADS for s in _TARGET_TAILS)
                    if t not in banned)
    contents = tuple(c for c in
                     (h + t for h in _CONTENT_HEADS for t in _CONTENT_TAILS)
                     if c not in banned)
    return paths, patterns, targets, contents


_PATHS, _PATTERNS, _TARGETS, _CONTENTS = _build()
_GLOBS = {"python": "*.py", "markdown": "*.md", "yaml": "*.yaml", "javascript": "*.js",
          "shell": "*.sh", "css": "*.css", "sql": "*.sql", "text": "*.txt",
          "config": "*.cfg", "ini": "*.ini"}

# TRAIN-ONLY PHRASINGS. The first version of this file mirrored the held-out
# set's wording, so the model could score by filling a template it had already
# seen — quarantine passed (no exact task overlap) while the PHRASINGS leaked,
# and the tuned arm hit a perfect 1.00 that measured memorisation. These are
# deliberately different surface forms, and `phrasing_overlap()` asserts no
# held-out task is reachable from them. The verbose/quiet cues differ too
# ("full logging" / "terse") so the bool must be inferred, not matched.
_READ = ("Fetch the contents of {p} for me.", "I need to look at {p}.",
         "Bring up {p}.", "Load {p} so I can review it.",
         "Give me the source of {p}.", "Retrieve {p}.",
         "Could you surface {p}?", "Dump {p} to the screen.")
_SEARCH = ("Which {lang} files mention {q}?",
           "Track down {q} inside the {lang} sources.",
           "Hunt for {q} among the {lang} files.",
           "Locate {q} in our {lang} code.",
           "Check the {lang} files for any {q}.",
           "Sweep the {lang} sources for {q}.")
_TESTS_V = ("Kick off the {t} suite with full logging.",
            "Trigger the {t} tests, detailed output.",
            "Start the {t} tests and print everything.",
            "Fire the {t} suite with all the detail.",
            "Launch {t} tests, full logging please.")
_TESTS_Q = ("Kick off the {t} suite, terse output.",
            "Trigger the {t} tests without detail.",
            "Start the {t} tests silently.",
            "Fire the {t} suite, keep it brief.",
            "Launch {t} tests, suppress the detail.")
_WRITE = ("Persist {c} into {p}.", "Drop {c} into the file {p}.",
          "Set the contents of {p} to {c}.", "Record {c} at {p}.",
          "Commit the value {c} to {p}.")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower()).strip()


def generate(n: int = 480, seed: int = 0, held_out=HELD_OUT) -> list:
    """Build `n` quarantined examples, evenly spread across the tool surface."""
    rng = random.Random(seed)
    banned = {_norm(t["task"]) for t in held_out}
    out, seen = [], set()
    per_tool = max(1, n // 4)

    def add(task, tool, args):
        k = _norm(task)
        # Quarantine enforced here, not assumed. A training task that is also an
        # eval task turns the gate into a memorisation check.
        if k in banned or k in seen:
            return False
        seen.add(k)
        out.append({"task": task, "tool": tool, "args": args})
        return True

    # The attempt budget must scale with the request. A fixed cap silently
    # truncates large sets, which would show up as a data-scaling result.
    budget = max(10_000, per_tool * 40)
    guard = 0
    while sum(1 for e in out if e["tool"] == "read_file") < per_tool and guard < budget:
        guard += 1
        add(rng.choice(_READ).format(p=rng.choice(_PATHS)), "read_file",
            {"path": None})  # filled below
    # rebuild read args properly (template carries the path)
    for e in out:
        if e["tool"] == "read_file":
            m = re.search(r"[\w/]+\.\w+|Makefile|setup\.cfg", e["task"])
            e["args"] = {"path": m.group(0)}

    guard = 0
    while sum(1 for e in out if e["tool"] == "search") < per_tool and guard < budget:
        guard += 1
        q, lang = rng.choice(_PATTERNS), rng.choice(list(_GLOBS))
        add(rng.choice(_SEARCH).format(q=q, lang=lang), "search",
            {"pattern": q, "glob": _GLOBS[lang]})

    guard = 0
    while sum(1 for e in out if e["tool"] == "run_tests") < per_tool and guard < budget:
        guard += 1
        t = rng.choice(_TARGETS)
        verbose = rng.random() < 0.5
        tpl = rng.choice(_TESTS_V if verbose else _TESTS_Q)
        add(tpl.format(t=t), "run_tests", {"target": t, "verbose": verbose})

    guard = 0
    while sum(1 for e in out if e["tool"] == "write_file") < per_tool and guard < budget:
        guard += 1
        p, c = rng.choice(_PATHS), rng.choice(_CONTENTS)
        add(rng.choice(_WRITE).format(p=p, c=c), "write_file", {"path": p, "content": c})

    return out


def to_mlx(examples) -> list:
    """Chat-format rows for `mlx_lm lora`.

    The prompt is `build_prompt` verbatim so the TRAIN prompt equals the EVAL
    prompt — the train/serve parity rule this project already had to learn once
    when deploying the classifier adapter.
    """
    rows = []
    for e in examples:
        rows.append({"messages": [
            {"role": "user", "content": build_prompt(e["task"])},
            {"role": "assistant",
             "content": json.dumps({"tool": e["tool"], "args": e["args"]})},
        ]})
    return rows


def phrasing_overlap(held_out=HELD_OUT) -> list:
    """Held-out tasks whose WORDING a training template could also produce.

    Exact-task quarantine is not enough: if training and eval share phrasings,
    the model can score by filling a template it has seen, and the gate measures
    memorisation. Compared on a skeleton with the variable parts masked out.
    """
    def skel(t):
        t = re.sub(r"[\w/]+\.\w+|Makefile|setup\.cfg", "@", t)
        t = re.sub(r"\b(python|markdown|yaml|javascript|shell|css|sql|text|js)\b", "@", t, flags=re.I)
        t = re.sub(r"'[^']*'|\"[^\"]*\"", "@", t)
        for tok in ("{p}", "{q}", "{lang}", "{t}", "{c}"):
            t = t.replace(tok, "@")
        return re.sub(r"[^a-z@ ]+", "", t.lower()).strip()
    train = {skel(x) for pool in (_READ, _SEARCH, _TESTS_V, _TESTS_Q, _WRITE) for x in pool}
    return [t["task"] for t in held_out if skel(t["task"]) in train]


def overlap_with_held_out(examples, held_out=HELD_OUT) -> list:
    banned = {_norm(t["task"]) for t in held_out}
    return [e["task"] for e in examples if _norm(e["task"]) in banned]


def main(argv=None) -> int:
    import argparse
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="S10 tool-call training data")
    p.add_argument("--n", type=int, default=480)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--valid-frac", type=float, default=0.1)
    p.add_argument("--out", default=str(repo / "evals/datasets/distilled/tool_calls"))
    a = p.parse_args(argv)

    ex = generate(a.n, seed=a.seed)
    bad = overlap_with_held_out(ex)
    if bad:
        raise SystemExit(f"QUARANTINE BREACH: {len(bad)} training tasks are in the eval set")
    leaked = phrasing_overlap()
    if leaked:
        raise SystemExit(f"PHRASING LEAK: {len(leaked)} held-out wordings are reachable "
                         f"from training templates, e.g. {leaked[:2]}")
    rows = to_mlx(ex)
    rng = random.Random(a.seed)
    rng.shuffle(rows)
    cut = max(1, int(len(rows) * a.valid_frac))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, part in (("valid", rows[:cut]), ("train", rows[cut:])):
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for r in part:
                fh.write(json.dumps(r) + "\n")
    from collections import Counter
    print(json.dumps({"generated": len(ex), "train": len(rows) - cut, "valid": cut,
                      "by_tool": dict(Counter(e["tool"] for e in ex)),
                      "held_out_overlap": 0, "out": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
