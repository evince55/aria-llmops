# aria-llmops

Harness-agnostic LLMOps telemetry, evals, and cost-aware routing for the Aria project.

## What it does
- **Telemetry ledger** (`telemetry/events.jsonl`): one normalized stream of model
  usage from Claude Code (transcript parser + SessionEnd hook) and opencode
  routing decisions (logged by `llmops.py`).
- **Evals** (`evals/`): router classification accuracy vs a labeled set, and a
  cost-only routing-efficiency replay over real usage.
- **Dashboard** (`dashboard/`): a self-contained static HTML view of spend,
  model mix, and routing — no server, no CDN.
- **Routing** (`llmops.py`): the existing ModelRouter/CostMonitor/CodingMemory.

## Usage
```bash
python3 telemetry.py ingest claude-code --all   # backfill existing sessions
python3 telemetry.py report
python3 telemetry.py eval all
python3 telemetry.py dashboard && open dashboard/index.html
python3 telemetry.py suggest                     # routing-tuning candidates
```

## Tests
```bash
.venv/bin/python -m pytest -q
```

## Claude Code hook
A `SessionEnd` hook in `~/.claude/settings.json` runs
`telemetry/hooks/claude_code_session_end.py` to auto-ingest each session.
