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


def _resolve_git_branch(raw_branch, cwd) -> Optional[str]:
    """Claude Code records `gitBranch` as the useless "HEAD" inside git worktrees
    / detached HEAD (~94% of our events), which destroys per-lane cost
    attribution. When the recorded branch is missing or "HEAD", derive a stable
    lane label from the cwd: the child of `.worktrees/` when the cwd is inside a
    worktree, else the working-directory basename. Pure + defensive — no
    subprocess, so it works even if the worktree has since been removed."""
    if raw_branch and raw_branch != "HEAD":
        return raw_branch
    if not cwd:
        return raw_branch
    parts = Path(cwd).parts
    if ".worktrees" in parts:
        i = parts.index(".worktrees")
        if i + 1 < len(parts):
            return parts[i + 1]
    return Path(cwd).name or raw_branch


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
    from telemetry.outcomes import outcome_from_transcript
    outcome = outcome_from_transcript(lines)  # heuristic per-session verdict (or None)

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
            git_branch=_resolve_git_branch(obj.get("gitBranch"), obj.get("cwd")),
            task_text=task_text,
            outcome=outcome,
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
