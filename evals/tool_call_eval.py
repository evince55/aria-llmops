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
from evals.tool_calls import TASKS, build_prompt, grade, score, validate  # noqa: E402

DEFAULT_BASE = "/Volumes/1TB NVMe/models/mlx-community/gemma-4-e2b-it-4bit"


def make_runner(base: str, adapter=None, max_tokens: int = 900):
    """Return `run(task) -> (text, finish_reason)` backed by a local MLX model."""
    import mlx_lm
    model, tok = mlx_lm.load(base, adapter_path=adapter)

    def run(task: str):
        prompt = build_prompt(task)
        apply = getattr(tok, "apply_chat_template", None)
        if apply is not None:
            try:
                prompt = apply([{"role": "user", "content": prompt}],
                               add_generation_prompt=True, tokenize=False)
            except Exception:
                pass
        text = mlx_lm.generate(model, tok, prompt, max_tokens=max_tokens, verbose=False)
        # mlx_lm.generate gives no finish_reason; approximate it by token budget.
        # Under-reporting truncation would be the dangerous direction, so this
        # errs toward flagging.
        n = len(tok.encode(text))
        return text, ("length" if n >= max_tokens - 2 else "stop")

    return run


def evaluate(run, tasks=TASKS) -> dict:
    problems = validate(tasks)
    if problems:
        raise SystemExit(f"task set is not gradable: {problems[:3]}")
    rows, t0 = [], time.time()
    for t in tasks:
        text, finish = run(t["task"])
        g = grade(text, t["tool"], t["args"], finish_reason=finish)
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
    p.add_argument("--max-tokens", type=int, default=900)
    p.add_argument("--out", default=str(repo / "logs/s10_eval.json"))
    a = p.parse_args(argv)

    res = evaluate(make_runner(a.base, a.adapter, a.max_tokens))
    res["arm"] = {"base": a.base, "adapter": a.adapter, "max_tokens": a.max_tokens}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2))
    print(json.dumps(res["summary"], indent=2))
    if not res["summary"]["sound"]:
        print("  ! run is NOT sound — truncation above threshold; raise --max-tokens",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
