#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook: shadow-log a route_decision for each
substantive user prompt so the flywheel gets (task -> tier -> outcome) intake
from Claude-driven work. Keyword classifier only — no model calls, no RAM
churn; the 9B/oMLX opinion is backfilled in batch by the harvester. Stamps
the session_id so telemetry/flywheel.py can join decisions to per-session
outcomes. Reads hook JSON on stdin. ALWAYS exits 0 so telemetry can never
disrupt a Claude Code session.

Upstreamed from MusicAppIOS/.claude/hooks/llmops_route_log.py (2026-07-12);
workspace settings.json points here now.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Make the repo importable regardless of where the hook is invoked from.
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

# Pasted logs/diffs can be huge; the classifier only reads the head anyway.
MAX_TASK_CHARS = 4000


def run(stdin_text: str) -> int:
    try:
        payload = json.loads(stdin_text)
        prompt = (payload.get("prompt") or "").strip()
        # Slash commands, memory shortcuts, bangs, and ack-length replies are
        # not tasks — logging them would pollute the flywheel dataset.
        if len(prompt) < 20 or prompt.startswith(("/", "#", "!")):
            return 0
        from llmops import ModelRouter

        ledger = os.environ.get("LLMOPS_LEDGER")
        router = ModelRouter(
            log_decisions=True,
            harness="claude-code",
            ledger=Path(ledger) if ledger else None,
        )
        router.route_task(
            prompt[:MAX_TASK_CHARS],
            estimated_tokens=max(500, len(prompt) // 4),
            session_id=payload.get("session_id"),
        )
    except Exception:
        # Telemetry must never break a session.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.stdin.read()))
