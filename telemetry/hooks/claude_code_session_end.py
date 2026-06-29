#!/usr/bin/env python3
"""Claude Code SessionEnd hook: ingest the just-finished session's transcript
into the LLMOps ledger. Reads hook JSON on stdin. ALWAYS exits 0 so a telemetry
failure can never disrupt a Claude Code session."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make the repo importable regardless of where the hook is invoked from.
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))


def run(stdin_text: str) -> int:
    try:
        from telemetry import ingest_claude_code as cc
        from telemetry import schema
        payload = json.loads(stdin_text)
        tpath = payload.get("transcript_path")
        if not tpath or not Path(tpath).exists():
            return 0
        ledger = Path(os.environ.get("LLMOPS_LEDGER", str(schema.LEDGER_DEFAULT)))
        cc.ingest([Path(tpath)], ledger=ledger)
    except Exception:
        # Telemetry must never break a session.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.stdin.read()))
