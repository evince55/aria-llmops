"""Run one iOS experiment cell: (bug, arm) -> builder run -> pixel grade.

The regime the cascade was designed for: the builder's only feedback loop is
./tools/run_app.sh — an xcodebuild + simctl install/launch + screenshot cycle
that takes ~1-3 minutes per iteration. No DOM, no console. Arms as in the web
round: A capture-only, B + tier-0 local VLM verify, C + tier-1 claude -p oracle.

After the builder finishes, the runner does its OWN build+launch+screenshot of
the final repo state and grades it with grade_ios.py (nearest-baseline pixel
classification) — the graded binary always matches the graded source.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ios_bugs import IOS_BUGS, inject_ios  # noqa: E402

HARNESS = Path(__file__).parent.resolve()
VENV_PY = "/Users/chait/MusicAppIOS/tools/llmops/.venv/bin/python"
CLAUDE = "/Users/chait/.local/bin/claude"
VLM_MODEL = "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"
BUILDER_MODEL = "opencode-go/minimax-m3"
BUILD_TIMEOUT_S = 1800
SIM = "booted"

RUN_APP_SH = """#!/bin/bash
# Build the app, install+launch on the simulator, capture a screenshot.
# This is the ONLY feedback loop: ~1-3 minutes per run. Build errors (if any)
# are shown filtered; the screenshot lands in tools/out/app.png.
NAME="${1:-app}"
mkdir -p tools/out
echo "building..."
xcodebuild -project Aria.xcodeproj -scheme "Aria - Music Browser" -configuration Debug \\
  -destination 'platform=iOS Simulator,name=iPhone 17' -derivedDataPath {dd} build 2>&1 \\
  | grep -E "error:|warning: .*never used|BUILD" | tail -20 | tee tools/out/build.log
if ! grep -q "BUILD SUCCEEDED" tools/out/build.log; then
  echo "BUILD FAILED — fix the errors above before re-running."
  exit 1
fi
xcrun simctl terminate {sim} {bid} 2>/dev/null
xcrun simctl install {sim} "{dd}/Build/Products/Debug-iphonesimulator/Aria - Music Browser.app"
xcrun simctl launch {sim} {bid} >/dev/null
sleep 6
xcrun simctl io {sim} screenshot "tools/out/$NAME.png" >/dev/null 2>&1
echo "app running; screenshot saved to tools/out/$NAME.png"
"""

VERIFY_SH = """#!/bin/bash
# Tier-0 verifier: local vision model answers YES/NO about the LATEST app
# screenshot (run ./tools/run_app.sh first to refresh it). 120s cap.
Q="$1"
[ -z "$Q" ] && echo "usage: ./tools/verify.sh \\"yes/no question\\"" && exit 1
[ ! -f tools/out/app.png ] && echo "no screenshot yet — run ./tools/run_app.sh first" && exit 1
sips -Z 1024 tools/out/app.png --out tools/out/verify.png >/dev/null 2>&1
ANS=$({venv_py} -c "
import subprocess, sys
try:
    r = subprocess.run(['{venv_py}', '-m', 'mlx_vlm', 'generate', '--model', '{vlm}',
                        '--image', 'tools/out/verify.png', '--prompt',
                        'Look at this iPhone app screenshot. Answer with exactly one word, YES or NO: ' + sys.argv[1],
                        '--max-tokens', '6', '--temperature', '0'],
                       capture_output=True, text=True, timeout=120)
    import re
    m = re.search(r'\\b(YES|NO)\\b', (r.stdout or '').upper())
    print(m.group(1) if m else 'UNCLEAR')
except subprocess.TimeoutExpired:
    print('UNCLEAR (verifier timed out)')
" "$Q")
[ -z "$ANS" ] && ANS="UNCLEAR"
echo "$(date +%H:%M:%S) Q: $Q -> $ANS" >> tools/out/verify.log
echo "$ANS"
"""

DIAGNOSE_SH = """#!/bin/bash
# Tier-1 oracle: read-only senior diagnosis from the latest app screenshot +
# the Swift sources. Budget: 3 calls. Verdict printed verbatim.
NOTE="$1"
mkdir -p tools/out
N=$(ls tools/out/esc*-oracle.json 2>/dev/null | wc -l | tr -d ' ')
if [ "$N" -ge 3 ]; then echo "ESCALATION BUDGET EXHAUSTED (3/3 used)"; exit 0; fi
[ ! -f tools/out/app.png ] && echo "no screenshot yet — run ./tools/run_app.sh first" && exit 1
cp tools/out/app.png "tools/out/esc$N.png"
{claude} -p "You are a senior SwiftUI debugging oracle. A junior agent is fixing this user-reported bug in the iOS app in the current directory: '{symptom}'. The junior's own note (untrusted, may be wrong): '$NOTE'. Evidence: the current rendered app screenshot at tools/out/esc$N.png (Read it and look very carefully at what is and is not visible), and the Swift sources (App/, Views/, Managers/, Models/). Diagnose the ROOT CAUSE. Reply ONLY with compact JSON: {\\"what_is_wrong\\": \\"...\\", \\"evidence\\": \\"what in the screenshot/code shows it\\", \\"files_to_examine\\": [\\"...\\"], \\"suggested_direction\\": \\"...\\"}" \\
  --allowedTools "Read" "Glob" "Grep" --max-turns 15 --output-format json \\
  > "tools/out/esc$N-oracle.json" 2>/dev/null
jq -r 'if .is_error then "ORACLE ERROR: " + (.subtype // "unknown") else .result end' "tools/out/esc$N-oracle.json"
"""

PROMPT_CORE = """You are working on a SwiftUI iOS app (Xcode project in the current directory; deploy target iOS 16.6, no third-party packages). A booted iPhone 17 simulator is available.

USER BUG REPORT: "{symptom}"

Your job: find the ROOT CAUSE and fix it with a MINIMAL change. Do not redesign anything.

IMPORTANT: this is a rendering bug — code reading alone is not sufficient, and there are no runtime logs for it. The ONLY way to see the app is ./tools/run_app.sh, which builds (takes 1-3 minutes), launches the app in the simulator, and saves a screenshot to tools/out/app.png. Budget your build cycles; verify against the rendered app before you finish.

Tools (run from the repo root; do not modify anything under tools/):
{tool_docs}

When you are finished, end your reply with exactly one final line:
DONE: <one line: root cause + what you changed>
or, if you cannot resolve it:
STUCK: <one line: what is blocking you>"""

TOOL_DOCS = {
    "A": """- ./tools/run_app.sh NAME — build + install + launch + screenshot to tools/out/NAME.png (default name: app). View the png if your tooling supports images.""",
    "B": """- ./tools/run_app.sh NAME — build + install + launch + screenshot to tools/out/NAME.png (default: app).
- ./tools/verify.sh "question" — an independent vision checker looks at the LATEST screenshot and answers strictly YES or NO. Ask positive presence questions (e.g. "Is the gray hint text under the No Favorites Yet heading clearly readable?"). REQUIRED: before claiming DONE, refresh the screenshot with run_app.sh and run a verify question that confirms the user's symptom is resolved; quote the question and answer in your final message.""",
    "C": """- ./tools/run_app.sh NAME — build + install + launch + screenshot to tools/out/NAME.png (default: app).
- ./tools/verify.sh "question" — an independent vision checker looks at the LATEST screenshot and answers strictly YES or NO. Ask positive presence questions. REQUIRED before claiming DONE (refresh the screenshot first; quote question and answer).
- ./tools/diagnose.sh "your one-line note" — escalates to a senior SwiftUI debugging oracle: it examines the latest screenshot and the sources and returns a root-cause diagnosis as JSON. Budget: 3 calls. Use it if your first fix attempt does not resolve the symptom or you are unsure of the cause. Treat its diagnosis as strong advice.""",
}


def sh(cmd, cwd=None, timeout=None):
    return subprocess.run(cmd, cwd=cwd, timeout=timeout, text=True, capture_output=True,
                          shell=isinstance(cmd, str))


def render(template: str, ctx: dict) -> str:
    for k, v in ctx.items():
        template = template.replace("{" + k + "}", str(v))
    return template


def build_and_shot(repo: Path, dd: Path, bid: str, out_png: Path) -> str:
    r = sh(["xcodebuild", "-project", "Aria.xcodeproj", "-scheme", "Aria - Music Browser",
            "-configuration", "Debug", "-destination", "platform=iOS Simulator,name=iPhone 17",
            "-derivedDataPath", str(dd), "build"], cwd=repo, timeout=900)
    if "BUILD SUCCEEDED" not in (r.stdout or ""):
        return "build-failed"
    sh(["xcrun", "simctl", "terminate", SIM, bid])
    sh(["xcrun", "simctl", "install", SIM, str(dd / "Build/Products/Debug-iphonesimulator/Aria - Music Browser.app")])
    sh(["xcrun", "simctl", "launch", SIM, bid])
    time.sleep(6)
    sh(["xcrun", "simctl", "io", SIM, "screenshot", str(out_png)])
    return "ok" if out_png.exists() else "shot-failed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bug", required=True, choices=sorted(IOS_BUGS))
    ap.add_argument("--arm", required=True, choices=["A", "B", "C"])
    ap.add_argument("--base", required=True)
    ap.add_argument("--dd", required=True, help="shared DerivedData path")
    ap.add_argument("--bid", required=True, help="bundle id")
    ap.add_argument("--pristine", required=True, help="pristine baseline png")
    ap.add_argument("--planted", required=True, help="planted baseline png for this bug")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    # HERMETICITY GUARD. The first iOS round was invalidated because the builder
    # used its own xcodebuildmcp (resolves by scheme) + absolute-path exploration
    # and escaped the sandbox — building, screenshotting, and EDITING the real
    # Aria repo instead of the planted copy. File-injected tools don't contain a
    # builder that brings its own MCP tooling. Until the sandbox is enforced below
    # the agent's tooling (strip xcodebuildmcp / worktree-as-scheme-target /
    # container), refuse to run and snapshot the real repo so any escape is caught.
    real_repo = Path("/Users/chait/MusicAppIOS/Aria_Music_Browser")
    real_head = sh(["git", "stash", "list"], cwd=real_repo)  # cheap liveness
    real_before = sh(["git", "status", "--porcelain"], cwd=real_repo).stdout
    if not __import__("os").environ.get("P2_IOS_ALLOW_UNSEALED"):
        raise SystemExit(
            "REFUSING: iOS harness is not hermetic (builder's xcodebuildmcp escapes "
            "the file sandbox). Set P2_IOS_ALLOW_UNSEALED=1 only after sealing the "
            "sandbox, and expect the post-run real-repo check below to enforce it.")

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "repo"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(a.base, work, symlinks=True)

    inject_ios(a.bug, work)
    sh(["git", "add", "-A"], cwd=work)
    sh(["git", "-c", "user.email=dev@local", "-c", "user.name=dev",
        "commit", "-qm", IOS_BUGS[a.bug]["commit"]], cwd=work)

    tools = work / "tools"
    tools.mkdir(exist_ok=True)
    ctx = dict(dd=a.dd, sim=SIM, bid=a.bid, venv_py=VENV_PY, vlm=VLM_MODEL, claude=CLAUDE,
               symptom=IOS_BUGS[a.bug]["symptom"].replace('"', "'"))
    (tools / "run_app.sh").write_text(render(RUN_APP_SH, ctx))
    if a.arm in ("B", "C"):
        (tools / "verify.sh").write_text(render(VERIFY_SH, ctx))
    if a.arm == "C":
        (tools / "diagnose.sh").write_text(render(DIAGNOSE_SH, ctx))
    for f in tools.glob("*.sh"):
        f.chmod(0o755)

    prompt = PROMPT_CORE.format(symptom=IOS_BUGS[a.bug]["symptom"], tool_docs=TOOL_DOCS[a.arm])
    (out / "prompt.txt").write_text(prompt)

    t0 = time.time()
    status = "completed"
    try:
        run = subprocess.run(["opencode", "run", "--auto", "-m", BUILDER_MODEL, prompt],
                             cwd=work, timeout=BUILD_TIMEOUT_S, text=True, capture_output=True)
        builder_log = (run.stdout or "") + "\n--- stderr ---\n" + (run.stderr or "")
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        raw = exc.stdout or ""
        builder_log = (raw.decode(errors="replace") if isinstance(raw, bytes) else raw) + "\n[TIMEOUT]"
    wall_s = round(time.time() - t0, 1)
    (out / "builder.log").write_text(builder_log)

    # Grade on a fresh build of the final source state.
    final_png = out / "final.png"
    build_state = build_and_shot(work, Path(a.dd), a.bid, final_png)
    if build_state == "ok":
        sh([VENV_PY, str(HARNESS / "grade_ios.py"), "--shot", str(final_png),
            "--pristine", a.pristine, "--planted", a.planted, "--bug", a.bug,
            "--out", str(out / "grade.json")])
        verdict = json.loads((out / "grade.json").read_text())
    else:
        verdict = {"pass": False, "build_state": build_state}
        (out / "grade.json").write_text(json.dumps(verdict))

    diff = sh(["git", "diff"], cwd=work).stdout or ""
    (out / "diff.patch").write_text(diff)

    clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", builder_log)
    claim_lines = [l.strip() for l in clean.splitlines()
                   if re.match(r"^(DONE|STUCK):", l.strip()) and "<one line" not in l]
    n_verify = len((tools / "out/verify.log").read_text().splitlines()) \
        if (tools / "out/verify.log").exists() else 0
    n_escalate = len(list((tools / "out").glob("esc*-oracle.json"))) if (tools / "out").exists() else 0
    n_builds = clean.count("BUILD SUCCEEDED") + clean.count("BUILD FAILED")

    summary = {
        "bug": a.bug, "arm": a.arm, "status": status, "wall_s": wall_s,
        "fixed": bool(verdict.get("pass")), "build_state": build_state,
        "claimed_done": any(l.startswith("DONE:") for l in claim_lines),
        "claimed_stuck": any(l.startswith("STUCK:") for l in claim_lines),
        "false_success": bool(any(l.startswith("DONE:") for l in claim_lines)
                              and not verdict.get("pass")),
        "verify_calls": n_verify, "escalations": n_escalate,
        "builder_build_cycles": n_builds,
        "claim": (claim_lines[-1][:220] if claim_lines else None),
        "diff_lines": len([l for l in diff.splitlines() if l.startswith(("+", "-"))]),
    }
    # Post-run hermeticity assertion: the real repo must be byte-for-byte what it
    # was before this cell. A difference means the builder escaped the sandbox and
    # the cell's results are contaminated (and the real repo needs restoring).
    real_after = sh(["git", "status", "--porcelain"], cwd=real_repo).stdout
    summary["real_repo_escaped"] = (real_after != real_before)
    if summary["real_repo_escaped"]:
        summary["fixed"] = None  # contaminated — verdict is meaningless
        (out / "CONTAMINATED").write_text(
            "real repo changed during this cell — results invalid; run "
            "`git -C /Users/chait/MusicAppIOS/Aria_Music_Browser status` and restore.\n"
            f"before:\n{real_before}\nafter:\n{real_after}\n")

    (out / "cell.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
