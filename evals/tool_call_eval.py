"""S10 evaluation runner — measure a model on the held-out tool-call set.

WHY THIS LOADS THE MODEL DIRECTLY INSTEAD OF USING THE SERVER. `mlx_lm server`
resolves models BY PATH ON DEMAND: request the base path and it serves the base,
silently ignoring `--adapter-path`. A first "tuned" run scored exactly the base's
0.70/0.90/0.90 because it *was* the base. Direct `mlx_lm.load(..., adapter_path=)`
removes the ambiguity — the arm you name is the arm you measure.

The same run also has to give the model room to finish: these are reasoning
models that emit a preamble before the JSON, and a tight `max_tokens` produces an
empty completion that looks like incapacity. `score()` reports `truncation_rate`
and flags a run `sound=False` above 5%, so a capped run cannot be mistaken for a
measurement.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.tool_calls import (  # noqa: E402
    HARD_TASKS, REGRESSION_TASKS, TASKS, WIDE_TASKS, build_prompt, grade,
    is_deterministic, operating_point, score, validate,
)

_SETS = {"standard": TASKS, "adversarial": HARD_TASKS,
         "wide": WIDE_TASKS, "regression": REGRESSION_TASKS}

DEFAULT_BASE = "/Volumes/1TB NVMe/models/mlx-community/gemma-4-e2b-it-4bit"


def apply_template(tok, prompt: str, thinking=None):
    """Render `prompt` through the model's chat template. Returns (text, applied).

    `thinking` names the template MODE, the way `operating_point` names the
    sampler. Qwen3.5's template opens a reasoning block by default and the
    tool-call training data contains no reasoning, so serving with it on asked
    the adapter for something it was never trained to produce: 661 tokens
    instead of 21, and 11 of 19 truncated rows graded correct because the parser
    scraped a draft out of an unfinished generation.

    `None` means "do not pass the kwarg" — inherit whatever the template does.
    That is what went wrong, so it is recorded as `None` rather than reported as
    a mode that was chosen. A template with no such kwarg (Gemma) also reports
    `None`: silently succeeding would let two arms be served differently while
    the metadata claimed they matched.
    """
    apply = getattr(tok, "apply_chat_template", None)
    if apply is None:
        return prompt, None
    msgs = [{"role": "user", "content": prompt}]
    if thinking is not None:
        try:
            return apply(msgs, add_generation_prompt=True, tokenize=False,
                         enable_thinking=thinking), thinking
        except TypeError:
            pass  # template has no thinking mode; fall through and record None
    try:
        return apply(msgs, add_generation_prompt=True, tokenize=False), None
    except Exception:
        return prompt, None


def card_point(base: str) -> dict:
    """The sampling config the model actually ships, read from its own files.

    Not guessed and not inherited from another model. If a checkpoint declares
    nothing, that is reported rather than filled in with a plausible number —
    an invented operating point is worse than an absent one.
    """
    for name in ("generation_config.json", "config.json"):
        path = Path(base) / name
        if not path.exists():
            continue
        cfg = json.loads(path.read_text())
        if "temperature" in cfg:
            return operating_point("card", temp=cfg["temperature"],
                                   top_p=cfg.get("top_p", 1.0), top_k=cfg.get("top_k", 0))
    raise SystemExit(f"{base} declares no sampling config; name the point explicitly")


def make_runner(base: str, adapter=None, max_tokens: int = 900, point=None, thinking=None):
    """Return `run(task) -> (text, finish_reason)` backed by a local MLX model."""
    import mlx_lm
    from mlx_lm.sample_utils import make_sampler
    model, tok = mlx_lm.load(base, adapter_path=adapter)
    point = point or operating_point("greedy")
    # Previously this called generate() with no sampler at all, which is greedy.
    # Greedy may well be right for a task with one correct answer — but it has to
    # be CHOSEN, and until now nothing recorded that anyone had chosen it.
    sampler = make_sampler(temp=point["temp"], top_p=point["top_p"], top_k=point["top_k"])

    def run(task: str):
        prompt, _ = apply_template(tok, build_prompt(task), thinking)
        text = mlx_lm.generate(model, tok, prompt, max_tokens=max_tokens, verbose=False,
                               sampler=sampler)
        # mlx_lm.generate gives no finish_reason; approximate it by token budget.
        # Under-reporting truncation would be the dangerous direction, so this
        # errs toward flagging.
        n = len(tok.encode(text))
        return text, ("length" if n >= max_tokens - 2 else "stop"), n

    return run


def evaluate(run, tasks=TASKS) -> dict:
    problems = validate(tasks)
    if problems:
        raise SystemExit(f"task set is not gradable: {problems[:3]}")
    rows, t0 = [], time.time()
    for t in tasks:
        text, finish, n_tokens = run(t["task"])
        g = grade(text, t["tool"], t["args"], finish_reason=finish, output_tokens=n_tokens)
        g["task"] = t["task"]
        rows.append(g)
        print(f"  {'OK ' if g['exact'] else '  x'} {t['tool']:11s} {t['task'][:50]}",
              file=sys.stderr)
    s = score(rows)
    s["wall_seconds"] = round(time.time() - t0, 1)
    return {"summary": s, "rows": rows}


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="S10: evaluate a model on held-out tool calls")
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--adapter", default=None, help="omit to measure the base model")
    p.add_argument("--set", choices=tuple(_SETS), default="standard")
    p.add_argument("--max-tokens", type=int, default=900)
    p.add_argument("--point", choices=("greedy", "card"), default="greedy",
                   help="'card' reads the checkpoint's own sampling config")
    p.add_argument("--thinking", choices=("on", "off"), default=None,
                   help="template reasoning mode; omit to inherit the template's default")
    p.add_argument("--runs", type=int, default=1,
                   help="repeats; above temperature 0 a single run is a sample, not a score")
    p.add_argument("--out", default=str(repo / "logs/s10_eval.json"))
    a = p.parse_args(argv)

    tasks = _SETS[a.set]
    point = operating_point("greedy") if a.point == "greedy" else card_point(a.base)
    runs = a.runs
    if runs > 1 and is_deterministic(point):
        print("  (deterministic point — one run is the measurement)", file=sys.stderr)
        runs = 1
    thinking = None if a.thinking is None else (a.thinking == "on")
    all_runs = [evaluate(make_runner(a.base, a.adapter, a.max_tokens, point, thinking), tasks)
                for _ in range(runs)]
    res = all_runs[0]
    scores = [r["summary"]["strict_accuracy"] for r in all_runs]
    res["runs"] = {"n_runs": len(scores), "scores": [round(s, 4) for s in scores],
                   "mean": round(sum(scores) / len(scores), 4),
                   "spread": round(max(scores) - min(scores), 4)}
    res["arm"] = {"base": a.base, "adapter": a.adapter, "set": a.set,
                  "interface": "prose", "max_tokens": a.max_tokens,
                  "operating_point": {"name": a.point, **point},
                  "template_thinking": a.thinking}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2))
    print(json.dumps(res["summary"], indent=2))
    if not res["summary"]["sound"]:
        print("  ! run is NOT sound — truncation above threshold; raise --max-tokens",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
