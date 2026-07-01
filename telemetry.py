#!/usr/bin/env python3
"""Aria LLMOps telemetry CLI. Subcommands: ingest, eval, dashboard, report, suggest.

Standard library only. Run: python3 telemetry.py <subcommand> ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from telemetry import ingest_claude_code as cc
from telemetry import schema


def _cmd_report(args) -> int:
    ledger = Path(args.ledger) if args.ledger else schema.LEDGER_DEFAULT
    events = schema.read_events(ledger=ledger)
    usage = [e for e in events if e.get("event") == "usage"]
    decisions = [e for e in events if e.get("event") == "route_decision"]
    from collections import defaultdict
    by_outcome: dict = defaultdict(lambda: {"events": 0, "imputed_usd": 0.0})
    for e in usage:
        b = by_outcome[e.get("outcome") if e.get("outcome") is not None else "unlabeled"]
        b["events"] += 1
        b["imputed_usd"] = round(b["imputed_usd"] + float(e.get("imputed_usd", 0) or 0), 4)
    print(json.dumps({
        "usage_events": len(usage),
        "route_decisions": len(decisions),
        "total_imputed_usd": round(sum(float(e.get("imputed_usd", 0) or 0) for e in usage), 4),
        "total_actual_usd": round(sum(float(e.get("actual_usd", 0) or 0) for e in usage), 4),
        "by_outcome": dict(by_outcome),
    }))
    return 0


def _cmd_eval(args) -> int:
    from evals.router_classification_eval import load_dataset, evaluate as cls_eval
    from evals.routing_efficiency_eval import evaluate as eff_eval
    ds = Path(__file__).parent / "evals" / "datasets" / "labeled_tasks.jsonl"
    out = {}
    if args.which in ("classification", "all"):
        out["classification"] = cls_eval(load_dataset(ds))
    if args.which in ("efficiency", "all"):
        ledger = Path(args.ledger) if args.ledger else schema.LEDGER_DEFAULT
        res = eff_eval(schema.read_events(ledger=ledger))
        res.pop("rows", None)
        out["efficiency"] = res
    print(json.dumps(out, indent=2))
    return 0


def _cmd_dashboard(args) -> int:
    from dashboard.generate import generate
    ledger = Path(args.ledger) if args.ledger else None
    out = Path(args.out) if args.out else None
    p = generate(ledger=ledger, out=out)
    print(json.dumps({"written": str(p)}))
    return 0


def _cmd_suggest(args) -> int:
    """Surface the worst classifier mismatches as routing-tuning candidates."""
    from evals.router_classification_eval import load_dataset
    from llmops import ModelRouter
    ds = Path(__file__).parent / "evals" / "datasets" / "labeled_tasks.jsonl"
    router = ModelRouter(log_decisions=False)
    mismatches = []
    for row in load_dataset(ds):
        pred = router.classify(row["task"])
        if pred != row["expected_tier"]:
            mismatches.append({"task": row["task"], "expected": row["expected_tier"], "predicted": pred})
    print(json.dumps({"mismatches": mismatches, "count": len(mismatches)}, indent=2))
    return 0


def _cmd_reprice(args) -> int:
    from telemetry.reprice import reprice
    ledger = Path(args.ledger) if args.ledger else schema.LEDGER_DEFAULT
    summary = reprice(ledger=ledger, write=args.write)
    if not args.write:
        summary["note"] = "dry-run — pass --write to rewrite the ledger"
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_backfill_outcomes(args) -> int:
    """Re-derive per-session outcomes from source transcripts and stamp them onto
    existing ledger events (older events were ingested before outcome inference
    existed). Dry-run by default; --write rewrites the ledger atomically."""
    import tempfile
    from telemetry.outcomes import outcome_from_transcript
    ledger = Path(args.ledger) if args.ledger else schema.LEDGER_DEFAULT
    project_dir = Path(args.project_dir) if args.project_dir else cc.DEFAULT_PROJECT_DIR

    session_outcome: dict = {}
    for p in cc.iter_project_transcripts(project_dir):
        lines = []
        with p.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    lines.append(json.loads(raw))
                except ValueError:
                    continue
        sid = next((o.get("sessionId") for o in lines if o.get("sessionId")), p.stem)
        oc = outcome_from_transcript(lines)
        if oc is not None:
            session_outcome[sid] = oc

    events = schema.read_events(ledger=ledger)
    changed = 0
    for e in events:
        if e.get("event") != "usage":
            continue
        oc = session_outcome.get(e.get("session_id"))
        if oc is not None and e.get("outcome") != oc:
            e["outcome"] = oc
            changed += 1

    if args.write and events:
        with tempfile.NamedTemporaryFile(
            "w", dir=str(ledger.parent), prefix=".events.", suffix=".tmp",
            delete=False, encoding="utf-8",
        ) as fh:
            tmp = Path(fh.name)
            for e in events:
                fh.write(json.dumps(e) + "\n")
        tmp.replace(ledger)

    summary = {
        "sessions_with_outcome": len(session_outcome),
        "events_updated": changed,
        "written": bool(args.write),
    }
    if not args.write:
        summary["note"] = "dry-run — pass --write to update the ledger"
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_ingest(args) -> int:
    ledger = Path(args.ledger) if args.ledger else schema.LEDGER_DEFAULT
    if args.source == "claude-code":
        if args.session:
            n = cc.ingest([Path(args.session)], ledger=ledger)
        else:
            project_dir = Path(args.project_dir) if args.project_dir else cc.DEFAULT_PROJECT_DIR
            paths = cc.iter_project_transcripts(project_dir)
            n = cc.ingest(paths, ledger=ledger)
        print(json.dumps({"ingested": n, "ledger": str(ledger)}))
        return 0
    if args.source == "opencode":
        print(json.dumps({"ingested": 0, "note": "opencode usage parsing deferred; see route_decision logging"}))
        return 0
    print(json.dumps({"error": f"unknown source {args.source}"}))
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="telemetry", description="Aria LLMOps telemetry CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="Ingest usage data into the ledger")
    ing.add_argument("source", choices=["claude-code", "opencode"])
    ing.add_argument("--all", action="store_true", help="Ingest every session in the project dir")
    ing.add_argument("--session", help="Ingest a single transcript file")
    ing.add_argument("--project-dir", help="Override the Claude Code project dir")
    ing.add_argument("--ledger", help="Override the ledger path")
    ing.set_defaults(func=_cmd_ingest)

    rep = sub.add_parser("report", help="Summarize the ledger")
    rep.add_argument("--ledger")
    rep.set_defaults(func=_cmd_report)

    ev = sub.add_parser("eval", help="Run evals")
    ev.add_argument("which", choices=["classification", "efficiency", "all"], default="all", nargs="?")
    ev.add_argument("--ledger")
    ev.set_defaults(func=_cmd_eval)

    dash = sub.add_parser("dashboard", help="Generate the static HTML dashboard")
    dash.add_argument("--ledger")
    dash.add_argument("--out")
    dash.set_defaults(func=_cmd_dashboard)

    sug = sub.add_parser("suggest", help="List classifier mismatches to tune routing")
    sug.set_defaults(func=_cmd_suggest)

    rp = sub.add_parser("reprice", help="Recompute imputed_usd on existing events at current rates")
    rp.add_argument("--ledger")
    rp.add_argument("--write", action="store_true", help="Rewrite the ledger (default: dry-run)")
    rp.set_defaults(func=_cmd_reprice)

    bo = sub.add_parser("backfill-outcomes", help="Stamp per-session outcomes onto existing events from transcripts")
    bo.add_argument("--ledger")
    bo.add_argument("--project-dir", help="Override the Claude Code project dir")
    bo.add_argument("--write", action="store_true", help="Rewrite the ledger (default: dry-run)")
    bo.set_defaults(func=_cmd_backfill_outcomes)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
