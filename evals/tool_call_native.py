"""S10 addendum — measure a served model through its NATIVE tool-calling interface.

WHY THIS EXISTS. The first Ornith-1.0-9B measurement asked a *tool-tuned* model
for freeform JSON in prose and read tool accuracy 0.69, with 2 of 20 replies
unparseable. That is a red flag for the PROMPT, not the model: Ornith is trained
against the OpenAI `tools` parameter, where the call is emitted through a
constrained decoding path instead of being written out as prose. The S10 result
therefore recorded Ornith as *indicative, not a verdict*, pending this run.

WHAT IS HELD CONSTANT. Same four-tool surface (asserted against `TOOLS` in
tests, not by eyeball), same held-out tasks, same `grade()`. The single variable
is the delivery mechanism. Anything else moving would make the two arms
incomparable, which is the failure this run exists to correct.

TWO INSTRUCTION STRENGTHS, because the prose arm's instruction was hard ("Reply
with ONE tool call and NOTHING else"):
  * `required` — the matched condition. The model must emit a call.
  * `auto`     — the realistic agentic condition, where declining is allowed.
A gap between them is itself a finding: it separates "cannot form the call" from
"chose not to call a tool."

Requires a llama.cpp server started with `--jinja` (without it the `tools`
parameter is ignored and this silently measures prose again — the run aborts
rather than reporting that number).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.tool_calls import (  # noqa: E402
    HARD_TASKS, REGRESSION_TASKS, TASKS, WIDE_TASKS, build_prompt, grade,
    native_tool_schema, parse_call, parse_native_call, score, validate,
)

_SETS = {"standard": TASKS, "adversarial": HARD_TASKS,
         "wide": WIDE_TASKS, "regression": REGRESSION_TASKS}

DEFAULT_URL = "http://127.0.0.1:8081/v1/chat/completions"


def call(url: str, model: str, task: str, tool_choice: str, max_tokens: int, timeout: int,
         interface: str = "native", temperature: float = 0.0):
    """One request. Returns (message, finish_reason).

    `interface="prose"` measures the SAME served model through the prompt-only
    path, so the format comparison can be run from one committed tool against
    one endpoint. Anything else varying between the two arms would reintroduce
    the confound this module exists to remove.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content":
                      task if interface == "native" else build_prompt(task)}],
        "max_tokens": max_tokens,
        # NOT a free parameter. A model has a specified operating temperature and
        # measuring it elsewhere is the same error as measuring it through the
        # wrong prompt format (finding 14): Ornith-1.0-9B is benchmarked at 1.0,
        # and the first S10 runs forced 0 - overriding the server's own --temp.
        "temperature": temperature,
        # llama.cpp treats 1.0 as "no penalty"; 0 is a degenerate divisor that
        # corrupts logits. This project lost two days to that once.
        "repeat_penalty": 1.0,
    }
    if interface == "native":
        payload["tools"] = native_tool_schema()
        payload["tool_choice"] = tool_choice
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        choice = json.load(fh)["choices"][0]
    return choice.get("message") or {}, choice.get("finish_reason")


def preflight(url: str, model: str, timeout: int = 120) -> None:
    """Abort unless the server actually honours `tools`.

    A build without `--jinja` accepts the parameter and ignores it, answering in
    prose. That would silently re-measure the arm this run is meant to replace,
    and the number would look like a result.
    """
    msg, finish = call(url, model, "Show me what's in src/auth.py.", "auto", 400, timeout)
    if parse_native_call(msg) is None:
        raise SystemExit(
            "server did not emit a native tool call (finish_reason="
            f"{finish!r}, content={str(msg.get('content'))[:120]!r}).\n"
            "Start llama-server with --jinja, or this run measures prose, not tools.")


def evaluate(url, model, tasks, tool_choice, max_tokens, timeout, interface="native",
             temperature=0.0) -> dict:
    problems = validate(tasks)
    if problems:
        raise SystemExit(f"task set is not gradable: {problems[:3]}")
    rows, t0 = [], time.time()
    for t in tasks:
        try:
            msg, finish = call(url, model, t["task"], tool_choice, max_tokens, timeout,
                               interface, temperature)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # A transport failure is NOT a model failure. Recording it as one
            # would let a flaky network read as incapacity.
            raise SystemExit(f"request failed on {t['task'][:40]!r}: {type(exc).__name__}: {exc}")
        got = (parse_native_call(msg) if interface == "native"
               else parse_call(msg.get("content") or ""))
        # Route both arms through the SAME grade() by re-serialising to the
        # common shape — a scoring difference then cannot explain a score gap.
        g = grade(json.dumps(got) if got else "", t["tool"], t["args"], finish_reason=finish)
        g["task"] = t["task"]
        rows.append(g)
        print(f"  {'OK ' if g['exact'] else '  x'} {t['tool']:11s} {t['task'][:48]}",
              file=sys.stderr)
    s = score(rows)
    s["wall_seconds"] = round(time.time() - t0, 1)
    return {"summary": s, "rows": rows}


def main(argv=None) -> int:
    repo = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description="S10: native tool-calling measurement")
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--model", required=True)
    p.add_argument("--set", choices=tuple(_SETS), default="standard")
    p.add_argument("--tool-choice", choices=("required", "auto"), default="required")
    p.add_argument("--interface", choices=("native", "prose"), default="native")
    p.add_argument("--temperature", type=float, default=0.0,
                   help="the MODEL'S specified temperature, not a tuning knob (Ornith: 1.0)")
    p.add_argument("--max-tokens", type=int, default=900)
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)

    tasks = _SETS[a.set]
    if a.interface == "native":
        preflight(a.url, a.model, a.timeout)
    res = evaluate(a.url, a.model, tasks, a.tool_choice, a.max_tokens, a.timeout, a.interface,
                   a.temperature)
    res["arm"] = {"model": a.model, "interface": a.interface, "set": a.set,
                  "tool_choice": a.tool_choice if a.interface == "native" else None,
                  "max_tokens": a.max_tokens, "temperature": a.temperature}
    out = Path(a.out or repo / f"logs/s10_{a.interface}_{a.set}_{a.tool_choice}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2))
    print(json.dumps(res["summary"], indent=2))
    if not res["summary"]["sound"]:
        print("  ! run is NOT sound — truncation above threshold", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
