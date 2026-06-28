#!/usr/bin/env python3
"""
Headroom context-compression eval for Aria's LLMOps workflow.

WHY THIS EXISTS
---------------
Aria's agent loop is xcodebuild-heavy (build -> read log -> fix -> rebuild).
Build logs are the dominant token sink. `headroom-ai` compresses tool outputs
before they reach the LLM. Before wiring it into the workflow we must prove two
things, because a compressor that drops the wrong line is worse than no
compressor:

  1. SAVINGS  - does it actually reduce tokens on *our* payloads?
  2. FIDELITY - does the actionable signal (compile error, test failure, linker
                error) survive compression, deterministically?

This script reproduces both claims. It is the evidence behind the integration
decision recorded in evals/headroom-eval.md.

INSTALL / RUN
-------------
    python3 -m venv .venv && . .venv/bin/activate
    pip install -r tools/llmops/evals/requirements.txt   # pins headroom-ai
    python3 tools/llmops/evals/headroom_fidelity_eval.py [--repo /path/to/MusicAppIOS] [--json out.json]

IMPORTANT — SUPPLY-CHAIN NOTE
-----------------------------
The package is `headroom-ai`, NOT `headroom`. `pip install headroom` installs an
UNRELATED, shell-executing "CLI AI assistant" (github.com/SUNKENDREAMS/headroom)
that collides on the same `headroom` import namespace. Always install the pinned
`headroom-ai` from requirements.txt; never bare-name-install.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODEL = "claude-sonnet-4-5-20250929"


def boilerplate_log(n: int, start: int = 0) -> str:
    """A realistic xcodebuild-style log: n compile units, each with a warning."""
    return "\n".join(
        f"CompileSwift normal arm64 .../File{i}.swift (in target 'Aria' from project 'Aria')\n"
        f"    swift-frontend -frontend -c -primary-file File{i}.swift "
        f"-target arm64-apple-ios16.6-simulator -O\n"
        f"warning: 'init(coder:)' is unavailable [File{i}.swift:{(i + start) * 3}:9]"
        for i in range(n)
    )


# Realistic Aria build/test failure signals. Each needle lists the substrings an
# agent MUST be able to recover from the compressed context to act correctly.
NEEDLES: dict[str, tuple[str, list[str]]] = {
    "compile_error": (
        "PlayerManager.swift:412:33: error: value of type 'PlayerManager' "
        "has no member 'crossfade'",
        ["PlayerManager.swift:412", "no member 'crossfade'"],
    ),
    "test_failure": (
        "Test Case '-[AriaTests.PlayerManagerTests testSeekAccuracy]' failed\n"
        "    XCTAssertEqual failed: (\"0.0\") is not equal to (\"5.0\") - seek did not advance",
        ["testSeekAccuracy", "is not equal to"],
    ),
    "linker_error": (
        "ld: Undefined symbol: _OBJC_CLASS_$_AVAudioEngine referenced from EQManager.o",
        ["Undefined symbol", "AVAudioEngine"],
    ),
}


def build_session(needle_text: str, position: str) -> list[dict]:
    """A 3-iteration build-fix loop. `position` puts the needle in the latest
    ('recent', which headroom protects) or first ('old', compressible) log."""
    msgs: list[dict] = []
    for r in range(3):
        msgs.append({"role": "assistant", "content": f"Build attempt {r + 1}; running xcodebuild..."})
        log = boilerplate_log(120, start=r * 120)
        if (position == "old" and r == 0) or (position == "recent" and r == 2):
            log = log + "\n" + needle_text
        msgs.append({"role": "tool", "content": log})
    msgs.append({"role": "user", "content": "What is the build error and which file/line?"})
    return msgs


def text_of(msgs: list[dict]) -> str:
    return "\n".join(
        (m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content")))
        for m in msgs
    )


def run(repo: Path) -> dict:
    from headroom import compress, CompressConfig  # imported here so --help works without the dep

    results: dict = {"savings_profile": [], "fidelity": []}

    # -- Part A: content-type compressibility CEILING -------------------------
    # Isolates "how compressible is this content type" free of session-position
    # confounds (protect_recent / recency), using a forced-aggressive config on
    # single payloads. Shows the core finding: repetitive machine output crushes;
    # dense source code has no slack and is ~0%. (Part B measures the realistic
    # in-session number, which is lower because recent context is protected.)
    swift = (repo / "Aria_Music_Browser/Managers/PlayerManager.swift").read_text(errors="replace")
    apppy = (repo / "backend/app.py").read_text(errors="replace")
    ceiling = CompressConfig(compress_user_messages=True, protect_recent=0,
                             min_tokens_to_compress=100, target_ratio=0.3)
    # FRAGILITY FINDING: SmartCrusher's single-payload compression is deterministic
    # but FORMAT-SENSITIVE. Blank-line-delimited repeated records (which real
    # xcodebuild output has) trigger array-style crushing (~98%); the identical log
    # with single-newline joins gets 0%. Source code has no slack and is 0% either
    # way. We surface all four so the writeup isn't built on a fragile best case.
    block = lambda i: (
        f"CompileSwift normal arm64 .../File{i}.swift (in target 'Aria' from project 'Aria')\n"
        f"    swift-frontend -frontend -c -primary-file File{i}.swift -O\n"
        f"warning: 'init(coder:)' is unavailable [File{i}.swift:{i * 3}:9]"
    )
    blocks = [block(i) for i in range(120)]
    profile = [
        ("PlayerManager.swift (real source)", swift),
        ("backend/app.py (real source)", apppy),
        ("xcodebuild log, single-newline", "\n".join(blocks)),
        ("xcodebuild log, blank-line-delim", "\n\n".join(blocks)),
    ]
    print("=== Part A: content-type compressibility ceiling (forced config) ===")
    print(f"{'payload':<38}{'before':>8}{'after':>8}{'saved':>7}")
    for name, text in profile:
        res = compress([{"role": "user", "content": text}], model=MODEL, config=ceiling)
        b, a = res.tokens_before, res.tokens_after
        pct = round(100 * (b - a) / b) if b else 0
        print(f"{name:<38}{b:>8}{a:>8}{pct:>6}%")
        results["savings_profile"].append(dict(payload=name, before=b, after=a, saved=pct))

    # -- Part B: fidelity (needle-in-haystack, 3x for determinism) -------------
    print("\n=== Part B: fidelity (does the actionable signal survive?) ===")
    print(f"{'needle':<16}{'position':<9}{'before':>8}{'after':>8}{'saved':>7}  {'kept?':<6}{'determ?'}")
    for nid, (ntext, required) in NEEDLES.items():
        for position in ("recent", "old"):
            keeps, afters, befores, saves = [], [], [], []
            for _ in range(3):
                res = compress(build_session(ntext, position), model=MODEL)
                befores.append(res.tokens_before)
                afters.append(res.tokens_after)
                saves.append(round(100 * (res.tokens_before - res.tokens_after) / res.tokens_before))
                keeps.append(all(s in text_of(res.messages) for s in required))
            determ = len(set(afters)) == 1 and len(set(keeps)) == 1
            kept = keeps[0]
            print(f"{nid:<16}{position:<9}{befores[0]:>8}{afters[0]:>8}{saves[0]:>6}%  "
                  f"{'KEPT' if kept else 'LOST':<6}{determ}")
            results["fidelity"].append(dict(needle=nid, position=position, before=befores[0],
                                            after=afters[0], saved=saves[0], kept=kept,
                                            deterministic=determ))

    # -- verdict ---------------------------------------------------------------
    fid = results["fidelity"]
    verdict = dict(
        all_signals_preserved=all(r["kept"] for r in fid),
        all_deterministic=all(r["deterministic"] for r in fid),
        recent_signal_preserved=all(r["kept"] for r in fid if r["position"] == "recent"),
        realistic_savings_pct=round(sum(r["saved"] for r in fid) / len(fid)),
    )
    results["verdict"] = verdict
    print("\n=== VERDICT ===")
    for k, v in verdict.items():
        print(f"  {k}: {v}")
    safe = verdict["all_signals_preserved"] and verdict["all_deterministic"]
    print(f"\n  SAFE TO INTEGRATE: {safe}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="/Users/chait/MusicAppIOS", type=Path,
                    help="MusicAppIOS repo root (default: %(default)s)")
    ap.add_argument("--json", type=Path, help="write full results JSON here")
    args = ap.parse_args()
    if not (args.repo / "Aria_Music_Browser").exists():
        print(f"error: {args.repo} does not look like the MusicAppIOS repo", file=sys.stderr)
        return 2
    results = run(args.repo)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
