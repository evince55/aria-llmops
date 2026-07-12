"""Interactive web dashboard for aria-llmops — stdlib http.server, no deps.

Serves dashboard/web/* and exposes the telemetry, router, classifier evals, and
the savings calculator as JSON endpoints. The interactive counterpart to the
static dashboard/generate.py; reuses the same real APIs.

Run from the repo root:  python3 dashboard/server.py   (then open http://127.0.0.1:7799)

Read-only: the router is built with log_decisions=False and only route_task is
used (pure decision, no model calls, no ledger writes).
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from telemetry import schema  # noqa: E402
from evals.routing_efficiency_eval import evaluate as efficiency_eval  # noqa: E402
from evals.router_classification_eval import (  # noqa: E402
    evaluate as classification_eval, load_dataset)
from calculator.savings_model import Params, compute  # noqa: E402
from llmops import ModelRouter  # noqa: E402
import runner  # noqa: E402  (sibling module — the task-runner data-gen loop)

WEB = Path(__file__).resolve().parent / "web"
DATASETS = REPO_ROOT / "evals" / "datasets"
LIVERUN = REPO_ROOT / "evals" / "live-runs" / "results.json"
PORT = int(os.environ.get("ARIA_DASH_PORT", "7799"))
HOST = os.environ.get("ARIA_DASH_HOST", "127.0.0.1")  # set 0.0.0.0 to reach it over the LAN/Tailscale

# One read-only router for live classification (no ledger writes). Wire the 9B
# classifier so the Router pane demos the PRODUCTION hybrid, not the keyword
# floor; classify_hybrid degrades to keyword automatically if the model is down.
def _build_router():
    try:
        from llmops import LocalLlamaClient, resolve_inference_config
        cfg = resolve_inference_config()
        cc = LocalLlamaClient(cfg["classifier_url"], cfg["classifier_model"], enable_thinking=False)
        return ModelRouter(log_decisions=False, use_model_classifier=True, classifier_client=cc)
    except Exception:
        return ModelRouter(log_decisions=False)  # keyword-only fallback


_ROUTER = _build_router()

# Scalar Params fields we let the calculator UI override (name -> caster).
_CALC_FIELDS = {
    "tasks_per_month": int, "minutes_per_task_human": float, "loaded_hourly_usd": float,
    "automatable_fraction": float, "calls_per_task": int, "tokens_in_per_call": int,
    "tokens_out_per_call": int, "human_review_fraction": float,
    "review_minutes_per_task": float, "local_infra_usd_month": float,
    "setup_fee_usd": float, "service_fee_usd_month": float,
}

STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "application/javascript; charset=utf-8"),
          "/runner.js": ("runner.js", "application/javascript; charset=utf-8"),
          "/explorer.js": ("explorer.js", "application/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}


def overview():
    events = schema.read_events()
    usage = [e for e in events if e.get("event") == "usage"]
    imputed = round(sum(float(e.get("imputed_usd", 0) or 0) for e in usage), 4)
    actual = round(sum(float(e.get("actual_usd", 0) or 0) for e in usage), 4)
    by_model = defaultdict(float)
    for e in usage:
        by_model[e.get("model", "unknown")] += float(e.get("imputed_usd", 0) or 0)
    eff = efficiency_eval(events)
    return {
        "imputed_usd": imputed, "actual_usd": actual, "saved_usd": round(imputed - actual, 4),
        "events": len(usage), "route_decisions": sum(1 for e in events if e.get("event") == "route_decision"),
        "local_first_pct": eff["local_first_sessions_pct"], "n_sessions": eff["n_sessions"],
        "by_model": sorted(({"model": m, "usd": round(v, 4)} for m, v in by_model.items()),
                           key=lambda x: -x["usd"]),
        "tier_dist": sorted(({"tier": t, "count": c} for t, c in eff["by_complexity"].items()),
                            key=lambda x: -x["count"]),
    }


def classification():
    out = {}
    for label, fname in (("prose_blind", "labeled_tasks_prose.jsonl"),
                         ("keyword_tuned", "labeled_tasks.jsonl")):
        path = DATASETS / fname
        if path.exists():
            out[label] = classification_eval(load_dataset(path))
    return out


def classify_task(task: str):
    task = (task or "").strip()
    if not task:
        return {"error": "empty task"}
    dec = _ROUTER.route_task(task, estimated_tokens=1500)  # pure decision, no writes
    tier, matched = _ROUTER.classify_hybrid(task)
    return {"tier": dec["complexity"], "keyword_matched": matched,
            "chosen_model": dec["model"], "reason": dec["reason"],
            "estimated_usd": dec["estimated_cost"], "alternatives": dec["alternatives"]}


def calculator(qs: dict):
    overrides = {}
    for name, cast in _CALC_FIELDS.items():
        if name in qs:
            try:
                overrides[name] = cast(qs[name][0])
            except (ValueError, IndexError):
                pass
    p = dataclasses.replace(Params(), **overrides)
    return compute(p, use_measured=True)


def liverun():
    if LIVERUN.exists():
        return json.loads(LIVERUN.read_text(encoding="utf-8"))
    return {"error": "no live-run results on disk"}


CLASSIFIER_STATUS = REPO_ROOT / "evals" / "results" / "classifier_status.json"


def classifier_status():
    """Latest classifier accuracy across all labeled datasets (written by
    evals/classifier_status.py). Lets the dashboard show live testing progress."""
    if CLASSIFIER_STATUS.exists():
        return json.loads(CLASSIFIER_STATUS.read_text(encoding="utf-8"))
    return {"error": "no classifier_status.json yet — run evals/classifier_status.py"}


def events_tail(qs: dict):
    limit = 50
    try:
        limit = max(1, min(500, int(qs.get("limit", ["50"])[0])))
    except (ValueError, IndexError):
        pass
    usage = [e for e in schema.read_events() if e.get("event") == "usage"]
    return {"events": usage[-limit:], "total": len(usage)}


def _norm_event(e: dict) -> dict:
    """Flatten a usage/route_decision event into one row shape the Explorer renders.
    usage and route_decision keep model/tier/cost under different keys — unify them."""
    ev = e.get("event")
    return {
        "ts": e.get("ts"),
        "event": ev,
        "harness": e.get("harness"),
        "model": e.get("model") or e.get("chosen_model"),
        "tier": e.get("complexity"),               # route_decision only
        "task_text": e.get("task_text"),
        "outcome": e.get("outcome"),               # usage only
        "in_tok": e.get("input_tokens"),
        "out_tok": e.get("output_tokens"),
        "usd": e.get("imputed_usd") if ev == "usage" else e.get("estimated_usd"),
        "actual_usd": e.get("actual_usd"),
        "cost_model": e.get("cost_model"),
    }


def ledger(qs: dict):
    """Filterable, faceted view of the raw telemetry ledger — the Runner's mirror:
    the Runner writes events, the Explorer reads them back for inspection/debugging.
    Facets are counted over the whole ledger (so every filter value is discoverable);
    rows + summary reflect the active filter."""
    def g(k, d=""):
        v = qs.get(k, [d])
        return (v[0] if v else d)

    f_event = g("event", "all")
    f_harness = g("harness", "all")
    f_model = g("model", "all")
    f_outcome = g("outcome", "all")
    q = g("q", "").strip().lower()
    try:
        limit = max(1, min(1000, int(g("limit", "100"))))
    except ValueError:
        limit = 100
    try:
        offset = max(0, int(g("offset", "0")))
    except ValueError:
        offset = 0

    events = schema.read_events()

    def _facet(key):
        c = defaultdict(int)
        for e in events:
            c[key(e)] += 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))

    facets = {
        "event": _facet(lambda e: e.get("event") or "?"),
        "harness": _facet(lambda e: e.get("harness") or "?"),
        "model": _facet(lambda e: e.get("model") or e.get("chosen_model") or "?"),
        "outcome": _facet(lambda e: str(e.get("outcome")) if e.get("event") == "usage" else "—"),
    }

    def keep(e):
        ev = e.get("event")
        if f_event != "all" and ev != f_event:
            return False
        if f_harness != "all" and (e.get("harness") or "?") != f_harness:
            return False
        if f_model != "all" and (e.get("model") or e.get("chosen_model") or "?") != f_model:
            return False
        if f_outcome != "all":
            if (str(e.get("outcome")) if ev == "usage" else "—") != f_outcome:
                return False
        if q and q not in (e.get("task_text") or "").lower():
            return False
        return True

    filtered = [e for e in events if keep(e)]
    usage_f = [e for e in filtered if e.get("event") == "usage"]
    summary = {
        "n": len(filtered),
        "imputed_usd": round(sum(float(e.get("imputed_usd", 0) or 0) for e in usage_f), 4),
        "actual_usd": round(sum(float(e.get("actual_usd", 0) or 0) for e in usage_f), 4),
        "in_tok": sum(int(e.get("input_tokens", 0) or 0) for e in usage_f),
        "out_tok": sum(int(e.get("output_tokens", 0) or 0) for e in usage_f),
    }
    rows = [_norm_event(e) for e in reversed(filtered)][offset:offset + limit]
    return {"rows": rows, "total": len(filtered), "facets": facets, "summary": summary,
            "filters": {"event": f_event, "harness": f_harness, "model": f_model,
                        "outcome": f_outcome, "q": q, "limit": limit, "offset": offset}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_api(self, fn, *args):
        try:
            self._send(200, fn(*args))
        except Exception as e:  # a broken endpoint must not take the page down
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        if path in STATIC:
            fname, ctype = STATIC[path]
            fpath = WEB / fname
            if fpath.is_file():
                self._send(200, fpath.read_bytes(), ctype)
            else:
                self._send(404, f"/* {fname} not built yet */", ctype)
            return
        if path == "/api/overview":
            return self._json_api(overview)
        if path == "/api/classification":
            return self._json_api(classification)
        if path == "/api/calculator":
            return self._json_api(calculator, qs)
        if path == "/api/liverun":
            return self._json_api(liverun)
        if path == "/api/classifier-status":
            return self._json_api(classifier_status)
        if path == "/api/events":
            return self._json_api(events_tail, qs)
        if path == "/api/runs":
            n = 25
            try:
                n = max(1, min(200, int(qs.get("limit", ["25"])[0])))
            except (ValueError, IndexError):
                pass
            return self._json_api(runner.recent_runs, n)
        if path == "/api/ledger":
            return self._json_api(ledger, qs)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if path == "/api/classify":
            return self._json_api(classify_task, body.get("task", ""))
        if path == "/api/run":
            return self._json_api(runner.run, body.get("task", ""), bool(body.get("execute", False)))
        if path == "/api/run/outcome":
            return self._json_api(runner.record_outcome, body.get("run_id"), body.get("outcome"))
        if path == "/api/dataset/capture":
            return self._json_api(runner.capture, body.get("task", ""), body.get("tier"))
        self._send(404, {"error": "not found"})


if __name__ == "__main__":
    WEB.mkdir(exist_ok=True)
    print(f"Aria LLMOps dashboard on http://{HOST}:{PORT}  (repo: {REPO_ROOT})")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
