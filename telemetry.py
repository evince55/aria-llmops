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
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
