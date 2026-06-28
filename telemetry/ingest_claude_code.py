"""Parse Claude Code session transcripts (~/.claude/projects/<proj>/*.jsonl)
into normalized `usage` events. Defensive: tolerates missing fields and skips
unparseable lines. No network."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from telemetry import pricing, schema

HARNESS = "claude-code"
TASK_TEXT_MAX = 500
DEFAULT_PROJECT_DIR = Path.home() / ".claude" / "projects" / "-Users-chait-MusicAppIOS"


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


def _first_task_text(lines: list) -> Optional[str]:
    for obj in lines:
        if obj.get("type") == "user":
            txt = _content_to_text(obj.get("message", {}).get("content"))
            if txt.strip():
                return txt.strip()[:TASK_TEXT_MAX]
    return None


def parse_transcript(path) -> list:
    path = Path(path)
    lines = []
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except ValueError:
                continue

    task_text = _first_task_text(lines)
    session_id = next((o.get("sessionId") for o in lines if o.get("sessionId")), path.stem)

    events = []
    for obj in lines:
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") or {}
        usage = msg.get("usage")
        model = msg.get("model")
        if not usage or not model or str(model).startswith("<"):
            continue  # skip synthetic / usage-less messages
        msg_id = obj.get("requestId") or obj.get("uuid")
        if not msg_id:
            continue
        in_t = int(usage.get("input_tokens", 0) or 0)
        out_t = int(usage.get("output_tokens", 0) or 0)
        cw_t = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cr_t = int(usage.get("cache_read_input_tokens", 0) or 0)
        events.append(schema.make_usage_event(
            harness=HARNESS,
            session_id=session_id,
            msg_id=msg_id,
            model=model,
            input_tokens=in_t,
            output_tokens=out_t,
            cache_write_tokens=cw_t,
            cache_read_tokens=cr_t,
            cost_model="subscription",
            actual_usd=0.0,
            imputed_usd=pricing.imputed_usd(
                model, input_tokens=in_t, output_tokens=out_t,
                cache_write_tokens=cw_t, cache_read_tokens=cr_t,
            ),
            ts=obj.get("timestamp"),
            cwd=obj.get("cwd"),
            git_branch=obj.get("gitBranch"),
            task_text=task_text,
        ))
    return events


def iter_project_transcripts(project_dir=DEFAULT_PROJECT_DIR) -> list:
    project_dir = Path(project_dir)
    if not project_dir.exists():
        return []
    return sorted(project_dir.glob("*.jsonl"))


def ingest(paths: Iterable, ledger=schema.LEDGER_DEFAULT) -> int:
    total = 0
    for p in paths:
        total += schema.append_events(parse_transcript(p), ledger=ledger)
    return total
