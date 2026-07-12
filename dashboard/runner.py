"""Task Runner — the dashboard's data-generation loop.

Routes (and optionally executes on the local model) a real task through the aria
router, then lets you grade the outcome and capture the task as a labeled
classifier example. Every run feeds the pipeline the project is starved for:
  - route_decision + graded `usage` events -> efficiency / quality evals + the
    calculator's measured defaults.
  - captured {task, expected_tier} -> classifier training/eval data.

Opus-built scaffolding (routing, safe execution, telemetry writing, outcome +
capture logging). The Runner UI is wired by the Ornith models.

Read-only-by-default: route_decision is logged on every run (routing behaviour is
data), but a `usage` event is written only when you GRADE a run — so the ledger's
usage half is always outcome-labelled, which is what the quality eval needs.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from collections import OrderedDict
from pathlib import Path

from llmops import ModelRouter
from telemetry import schema
from telemetry.pricing import imputed_usd

HARNESS = "dashboard-runner"
RUNNER_MAX_TOKENS = int(os.environ.get("RUNNER_MAX_TOKENS", "512"))
CAPTURED = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "labeled_captured.jsonl"
_TIERS = ("SIMPLE", "MODERATE", "COMPLEX", "CRITICAL")
_PENDING_MAX = 200

_lock = threading.Lock()
_pending = OrderedDict()  # run_id -> full run record (held until graded)


def _build_router():
    """Model-classifier router with auto-logging OFF — we log manually so usage
    events carry the graded outcome. Degrades to keyword-only if the model is down."""
    try:
        from llmops import LocalLlamaClient, resolve_inference_config
        cfg = resolve_inference_config()
        cc = LocalLlamaClient(cfg["classifier_url"], cfg["classifier_model"], enable_thinking=False)
        return ModelRouter(log_decisions=False, use_model_classifier=True,
                           classifier_client=cc, harness=HARNESS)
    except Exception:
        return ModelRouter(log_decisions=False, harness=HARNESS)


_ROUTER = _build_router()


def _log_route_decision(task, result):
    try:
        schema.append_events([schema.make_route_decision_event(
            harness=HARNESS, task_text=task, complexity=result["complexity"],
            chosen_model=result["model"], estimated_usd=result["estimated_cost"],
            alternatives=result.get("alternatives", []))])
    except Exception:
        pass


def run(task, execute=False):
    """Route (and optionally execute) a task. Logs the route_decision, holds the
    run in memory keyed by run_id until it's graded. Returns the decision view."""
    task = (task or "").strip()
    if not task:
        return {"error": "empty task"}
    try:
        if execute:
            result = _ROUTER.run_task(task, max_tokens=RUNNER_MAX_TOKENS)
        else:
            result = dict(_ROUTER.route_task(task))
            result["executed"] = False
    except Exception as e:
        return {"error": "routing failed: " + str(e)[:120]}
    _log_route_decision(task, result)

    run_id = uuid.uuid4().hex[:12]
    record = {
        "run_id": run_id, "session_id": "runner-" + run_id, "task": task,
        "tier": result.get("complexity"), "model": result.get("model"),
        "estimated_usd": result.get("estimated_cost"),
        "reason": result.get("reason"), "alternatives": result.get("alternatives", []),
        "executed": result.get("executed", False),
        "output": result.get("output"), "exec_error": result.get("error"),
        "usage": result.get("usage") or {},
    }
    with _lock:
        _pending[run_id] = record
        while len(_pending) > _PENDING_MAX:
            _pending.popitem(last=False)
    return {k: record[k] for k in ("run_id", "task", "tier", "model", "estimated_usd", "reason",
                                   "alternatives", "executed", "output", "exec_error", "usage")}


def record_outcome(run_id, outcome):
    """Grade a held run success|failure -> write an outcome-labelled usage event."""
    if outcome not in ("success", "failure"):
        return {"error": "outcome must be 'success' or 'failure'"}
    with _lock:
        rec = _pending.get(run_id)
    if not rec:
        return {"error": "unknown or expired run_id"}
    usage = rec.get("usage") or {}
    in_t = int(usage.get("input_tokens", 0) or 0)
    out_t = int(usage.get("output_tokens", 0) or 0)
    model = rec["model"] or "unknown"
    try:
        schema.append_events([schema.make_usage_event(
            harness=HARNESS, session_id=rec["session_id"], msg_id="run",
            model=model, input_tokens=in_t, output_tokens=out_t,
            cost_model="local" if model.startswith("llama-cpp") else "cloud",
            imputed_usd=imputed_usd(model, input_tokens=in_t, output_tokens=out_t),
            task_text=rec["task"], outcome=outcome)])
    except Exception as e:
        return {"error": "log failed: " + str(e)[:100]}
    with _lock:
        _pending.pop(run_id, None)
    return {"ok": True, "run_id": run_id, "outcome": outcome}


def capture(task, tier):
    """Append a task + corrected tier label to the captured classifier dataset."""
    task = (task or "").strip()
    tier = (tier or "").strip().upper()
    if not task or tier not in _TIERS:
        return {"error": "need a task and a valid tier (SIMPLE/MODERATE/COMPLEX/CRITICAL)"}
    try:
        CAPTURED.parent.mkdir(parents=True, exist_ok=True)
        with open(CAPTURED, "a", encoding="utf-8") as f:
            f.write(json.dumps({"task": task[:2000], "expected_tier": tier,
                                "source": "dashboard-capture"}) + "\n")
        total = sum(1 for line in open(CAPTURED, encoding="utf-8") if line.strip())
    except Exception as e:
        return {"error": str(e)[:100]}
    return {"ok": True, "captured_total": total}


def recent_runs(limit=25):
    """Recent runner events (route_decisions + graded usage) from the ledger."""
    try:
        events = [e for e in schema.read_events() if e.get("harness") == HARNESS]
    except Exception:
        return {"runs": [], "total": 0}
    return {"runs": events[-limit:][::-1], "total": len(events)}
