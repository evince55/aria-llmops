"""Run one experiment cell: (bug, arm) -> builder run -> deterministic grade.

Arms differ ONLY in the verification tooling dropped into ./tools of the
builder's repo copy:
  A: screenshot.sh                       (capture only — the original failure mode)
  B: + verify.sh   (tier-0: local Qwen2.5-VL-3B answers YES/NO about a fresh shot)
  C: + diagnose.sh (tier-1: headless `claude -p` oracle on the Max plan,
                    read-only, 3-call budget, verdict relayed VERBATIM)

The builder is always opencode-go/minimax-m3 (owner constraint: opencode-go
only). The grader (grade.js) is never visible to the builder and asserts the
rendered fixed state, not claims.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bugs import BUGS, inject  # noqa: E402

HARNESS = Path(__file__).parent.resolve()
VENV_PY = "/Users/chait/MusicAppIOS/tools/llmops/.venv/bin/python"
CLAUDE = "/Users/chait/.local/bin/claude"
VLM_MODEL = "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"
BUILDER_MODEL = "opencode-go/minimax-m3"
BUILD_TIMEOUT_S = 720

SCREENSHOT_SH = """#!/bin/bash
# Capture the rendered page: screenshot + browser console.
NAME="${1:-shot}"
mkdir -p tools/out
node {harness}/shot.js http://localhost:{port}/ "tools/out/$NAME.png" "tools/out/$NAME-console.txt"
echo "saved tools/out/$NAME.png and tools/out/$NAME-console.txt"
"""

VERIFY_SH = """#!/bin/bash
# Tier-0 verifier: a local vision model answers YES/NO about a FRESH screenshot.
Q="$1"
[ -z "$Q" ] && echo "usage: ./tools/verify.sh \\"yes/no question about the page\\"" && exit 1
mkdir -p tools/out
node {harness}/shot.js http://localhost:{port}/ tools/out/verify.png tools/out/verify-console.txt >/dev/null 2>&1
# 120s hard cap: a stalled local model must degrade to UNCLEAR, never block
# the builder (observed: an mlx-vlm load stall deadlocked an otherwise-complete
# run against the verify-before-DONE protocol).
ANS=$({venv_py} -c "
import subprocess, sys
try:
    r = subprocess.run(['{venv_py}', '-m', 'mlx_vlm', 'generate', '--model', '{vlm}',
                        '--image', 'tools/out/verify.png', '--prompt',
                        'Look at this webpage screenshot. Answer with exactly one word, YES or NO: ' + sys.argv[1],
                        '--max-tokens', '6', '--temperature', '0'],
                       capture_output=True, text=True, timeout=120)
    import re
    m = re.search(r'\b(YES|NO)\b', (r.stdout or '').upper())
    print(m.group(1) if m else 'UNCLEAR')
except subprocess.TimeoutExpired:
    print('UNCLEAR (verifier timed out)')
" "$Q")
[ -z "$ANS" ] && ANS="UNCLEAR"
echo "$(date +%H:%M:%S) Q: $Q -> $ANS" >> tools/out/verify.log
echo "$ANS"
"""

DIAGNOSE_SH = """#!/bin/bash
# Tier-1 oracle: read-only senior diagnosis from a fresh screenshot + console
# + repo. Budget: 3 calls. Output is the oracle's verdict, verbatim.
NOTE="$1"
mkdir -p tools/out
N=$(ls tools/out/esc*-oracle.json 2>/dev/null | wc -l | tr -d ' ')
if [ "$N" -ge 3 ]; then echo "ESCALATION BUDGET EXHAUSTED (3/3 used)"; exit 0; fi
node {harness}/shot.js http://localhost:{port}/ "tools/out/esc$N.png" "tools/out/esc$N-console.txt" >/dev/null 2>&1
{claude} -p "You are a senior frontend debugging oracle. A junior agent is fixing this user-reported bug in the static site in the current directory: '{symptom}'. The junior's own note (untrusted, may be wrong): '$NOTE'. Evidence to examine: the rendered screenshot at tools/out/esc$N.png (Read it and look carefully), the browser console at tools/out/esc$N-console.txt, and the repo itself (index.html, css/, js/). Diagnose the ROOT CAUSE of the user's symptom. Reply ONLY with compact JSON: {{\\"what_is_wrong\\": \\"...\\", \\"evidence\\": \\"what in the screenshot/console/code shows it\\", \\"files_to_examine\\": [\\"...\\"], \\"suggested_direction\\": \\"...\\"}}" \\
  --allowedTools "Read" "Glob" "Grep" --max-turns 15 --output-format json \\
  > "tools/out/esc$N-oracle.json" 2>/dev/null
jq -r 'if .is_error then "ORACLE ERROR: " + (.subtype // "unknown") else .result end' "tools/out/esc$N-oracle.json"
"""

PROMPT_CORE = """You are working on a static web project (vanilla JS/CSS PWA, no build step) located in the current directory and served at http://localhost:{port}/ (already running; plain file serving, so a browser reload reflects saved edits immediately).

USER BUG REPORT: "{symptom}"

Your job: find the ROOT CAUSE and fix it with a MINIMAL change. Do not redesign anything; the rest of the page must stay exactly as it is.

IMPORTANT: this is a rendering bug — reading code alone is not sufficient. You must verify against the actually rendered page before you finish.

Verification tools available (run from the repo root; do not modify anything under tools/):
{tool_docs}

When you are finished, end your reply with exactly one final line:
DONE: <one line: root cause + what you changed>
or, if you cannot resolve it:
STUCK: <one line: what is blocking you>"""

TOOL_DOCS = {
    "A": """- ./tools/screenshot.sh NAME — captures the page to tools/out/NAME.png and the browser console (errors, failed requests) to tools/out/NAME-console.txt. Read the console file; view the png if your tooling supports images.""",
    "B": """- ./tools/screenshot.sh NAME — captures the page to tools/out/NAME.png and the browser console to tools/out/NAME-console.txt.
- ./tools/verify.sh "question" — an independent vision checker looks at a FRESH screenshot of the page and answers strictly YES or NO. Use factual visual questions, e.g. ./tools/verify.sh "Is a tagline visible between the large name and the paragraph?". REQUIRED: before claiming DONE you must run verify.sh with a question that confirms the user's symptom is resolved, and it must answer in your favour. Quote the question and answer in your final message.""",
    "C": """- ./tools/screenshot.sh NAME — captures the page to tools/out/NAME.png and the browser console to tools/out/NAME-console.txt.
- ./tools/verify.sh "question" — an independent vision checker looks at a FRESH screenshot and answers strictly YES or NO. REQUIRED: before claiming DONE you must run verify.sh with a question that confirms the user's symptom is resolved, and it must answer in your favour. Quote the question and answer in your final message.
- ./tools/diagnose.sh "your one-line note on what you observe" — escalates to a senior debugging oracle: it examines a fresh screenshot, the console, and the code, and returns a root-cause diagnosis as JSON. Budget: 3 calls. Use it if your first fix attempt does not resolve the symptom, or if you are unsure of the cause. Treat its diagnosis as strong advice.""",
}


def sh(cmd, cwd=None, timeout=None, capture=True):
    return subprocess.run(cmd, cwd=cwd, timeout=timeout, text=True,
                          capture_output=capture, shell=isinstance(cmd, str))


def render(template: str, ctx: dict) -> str:
    """Literal {key} substitution that leaves bash's ${...} and JSON braces
    alone — str.format chokes on both."""
    for k, v in ctx.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bug", required=True, choices=sorted(BUGS))
    ap.add_argument("--arm", required=True, choices=["A", "B", "C"])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--base", required=True, help="pristine repo clone")
    ap.add_argument("--out", required=True, help="cell output dir")
    a = ap.parse_args()

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "repo"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(a.base, work, symlinks=True)

    # Plant the bug and commit it, so the builder's `git diff` shows only
    # the builder's own changes.
    inject(a.bug, work)
    sh(["git", "add", "-A"], cwd=work)
    sh(["git", "-c", "user.email=dev@local", "-c", "user.name=dev",
        "commit", "-qm", BUGS[a.bug]["commit"]], cwd=work)

    # Arm tooling
    tools = work / "tools"
    tools.mkdir(exist_ok=True)
    ctx = dict(harness=str(HARNESS), port=a.port, venv_py=VENV_PY,
               vlm=VLM_MODEL, claude=CLAUDE, symptom=BUGS[a.bug]["symptom"].replace('"', "'"))
    (tools / "screenshot.sh").write_text(render(SCREENSHOT_SH, ctx))
    if a.arm in ("B", "C"):
        (tools / "verify.sh").write_text(render(VERIFY_SH, ctx))
    if a.arm == "C":
        (tools / "diagnose.sh").write_text(render(DIAGNOSE_SH, ctx))
    for f in tools.glob("*.sh"):
        f.chmod(0o755)

    server = subprocess.Popen([sys.executable, "-m", "http.server", str(a.port)],
                              cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    try:
        # Pre-grade: the bug must reproduce, or the cell is void.
        pre = out / "pre-grade.json"
        sh(["node", str(HARNESS / "grade.js"), f"http://localhost:{a.port}/", a.bug, str(pre)])
        pre_pass = json.loads(pre.read_text())["pass"]
        if pre_pass:
            raise RuntimeError(f"bug {a.bug} did not reproduce (pre-grade passed)")

        prompt = PROMPT_CORE.format(port=a.port, symptom=BUGS[a.bug]["symptom"],
                                    tool_docs=TOOL_DOCS[a.arm])
        (out / "prompt.txt").write_text(prompt)

        t0 = time.time()
        status = "completed"
        try:
            run = subprocess.run(
                ["opencode", "run", "--auto", "-m", BUILDER_MODEL, prompt],
                cwd=work, timeout=BUILD_TIMEOUT_S, text=True, capture_output=True)
            builder_log = (run.stdout or "") + "\n--- stderr ---\n" + (run.stderr or "")
        except subprocess.TimeoutExpired as exc:
            status = "timeout"
            builder_log = ((exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes)
                           else (exc.stdout or "")) + "\n[TIMEOUT]"
        wall_s = round(time.time() - t0, 1)
        (out / "builder.log").write_text(builder_log)

        # Post-run measurement
        sh(["node", str(HARNESS / "shot.js"), f"http://localhost:{a.port}/",
            str(out / "final.png"), str(out / "final-console.txt")])
        post = out / "grade.json"
        sh(["node", str(HARNESS / "grade.js"), f"http://localhost:{a.port}/", a.bug, str(post)])
        verdict = json.loads(post.read_text())

        diff = sh(["git", "diff", "--stat"], cwd=work).stdout
        (out / "diff.txt").write_text(diff or "(no changes)\n")
        full_diff = sh(["git", "diff"], cwd=work).stdout
        (out / "diff.patch").write_text(full_diff or "")

        import re as _re
        clean = _re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", builder_log)
        claim_lines = [l.strip() for l in clean.splitlines()
                       if _re.match(r"^(DONE|STUCK):", l.strip())
                       and "<one line" not in l]  # exclude prompt-template echoes
        claimed_done = any(l.startswith("DONE:") for l in claim_lines)
        claimed_stuck = any(l.startswith("STUCK:") for l in claim_lines)
        n_verify = len((work / "tools/out/verify.log").read_text().splitlines()) \
            if (work / "tools/out/verify.log").exists() else 0
        n_escalate = len(list((work / "tools/out").glob("esc*-oracle.json"))) \
            if (work / "tools/out").exists() else 0
        oracle_usage = []
        for oj in sorted((work / "tools/out").glob("esc*-oracle.json")) if (work / "tools/out").exists() else []:
            try:
                j = json.loads(oj.read_text())
                oracle_usage.append({"out_tokens": j.get("usage", {}).get("output_tokens"),
                                     "turns": j.get("num_turns"), "is_error": j.get("is_error")})
            except Exception:
                oracle_usage.append({"parse_error": True})

        summary = {
            "bug": a.bug, "arm": a.arm, "status": status, "wall_s": wall_s,
            "fixed": verdict["pass"], "claimed_done": claimed_done,
            "claimed_stuck": claimed_stuck,
            "false_success": bool(claimed_done and not verdict["pass"]),
            "verify_calls": n_verify, "escalations": n_escalate,
            "oracle_usage": oracle_usage,
            "claim": (claim_lines[-1][:220] if claim_lines else None),
            "diff_stat": (diff or "").strip().splitlines()[-1:] ,
        }
        (out / "cell.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary))
        return 0
    finally:
        server.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
