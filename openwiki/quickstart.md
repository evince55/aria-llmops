# aria-llmops — Quickstart

A **standard-library-only** LLMOps layer: cost-aware model routing, a telemetry
ledger, imputed-cost accounting, outcome grading, an eval suite, a static
dashboard, and a business savings model — all measured on the system's own real
usage. The Aria iOS music app is the test bed; this repo is the deliverable.

**Runtime:** Python 3.9+, zero third-party runtime deps. pytest is dev-only.

## The loop

Everything here exists to close one loop:

```
  measure  ->  route  ->  execute  ->  grade  ->  evaluate  ->  tune
  (ledger)    (router)   (local 35B)   (9B)       (evals)
```

1. **Measure** — every model call lands in one idempotent JSONL ledger with
   token counts and imputed USD at list rates.
2. **Route** — `ModelRouter` classifies task complexity (keyword-first +
   9B-rescue hybrid) and picks the cheapest viable model, gated by rolling
   5h/7d/30d budgets.
3. **Execute** — local tiers run on a self-hosted 35B MoE behind a single
   llama-swap endpoint; cloud tiers are decided (and priced) but not executed
   from this process.
4. **Grade** — session outcomes come from user reactions: keyword heuristic
   first, a 9B model grader for the inconclusive middle.
5. **Evaluate** — the evals turn the ledger + labeled datasets into accuracy,
   efficiency, and quality numbers, including failure modes.

See [Workflows](workflows/overview.md) for the full step-by-step with CLI
commands.

## Architecture at a glance

```
 Claude Code sessions      opencode / CLI            llmops-local (run_task)
        | SessionEnd hook         | route_decision            | usage
        v                         v                           v
   telemetry/ingest_claude_code -----> telemetry/events.jsonl (append-only,
        (idempotent, defensive)          idempotent ledger; gitignored data)
                                                    |
        +--------------------+----------------------+-------------------+
        v                    v                      v                   v
  telemetry.py report   evals/ (accuracy,      dashboard/          reprice /
                        efficiency, quality)   (static HTML)       backfill-outcomes

  ModelRouter (llmops.py)
    classify_hybrid: keyword-first, 9B-rescue on keyword default
    CostMonitor: rolling 5h/7d/30d windows, 80% force-local gate
    execution: LocalLlamaClient -> llama-swap :8080
               qwen3.6-35b (executor) | 9b-mythos (classifier)

  calculator/ (savings model)  <- measured defaults from the live run
```

See [Architecture](architecture/overview.md) for a detailed walkthrough of every
component.

## Key source files

| File | What it is |
|---|---|
| `llmops.py` | `ModelRouter`, `CostMonitor`, `CodingMemory`, `LocalLlamaClient`, `ModelClassifier`, CLI |
| `telemetry.py` | Telemetry CLI: `ingest`, `report`, `eval`, `dashboard`, `suggest`, `reprice`, `backfill-outcomes` |
| `telemetry/schema.py` | Event constructors (`make_usage_event`, `make_route_decision_event`), idempotent append, `TASK_TEXT_MAX=500` |
| `telemetry/pricing.py` | Per-model USD/1M-token rates, `imputed_usd()` calculator |
| `telemetry/outcomes.py` | Session outcome inference: keyword heuristic + optional 9B model grader |
| `telemetry/ingest_claude_code.py` | Claude Code transcript parser → ledger usage events |
| `telemetry/hooks/claude_code_session_end.py` | SessionEnd hook: auto-ingest on session close |
| `telemetry/reprice.py` | Recompute `imputed_usd` at current pricing rates |
| `dashboard/server.py` | Interactive dashboard server (stdlib `http.server`, port 7799) — 5 panes, 11 JSON API endpoints |
| `dashboard/runner.py` | Task-runner data-gen loop for the Runner pane; `run_batch()` + `list_datasets()` for the Batch pane |
| `dashboard/generate.py` | Static self-contained HTML dashboard generator |
| `dashboard/web/explorer.js` | Ledger Explorer pane: faceted, paged ledger viewer |
| `dashboard/web/batch.js` | Batch Runner pane: dataset selection, confusion matrix, per-task agreement |
| `calculator/savings_model.py` | Business savings model: human vs naive-AI vs routed-AI comparison |
| `headroom-proxy.sh` | Launcher for the Headroom context-compression proxy |
| `evals/` | Classification, efficiency, quality, and live-A/B eval scripts |
| `tests/` | 29 pytest tests covering router, schema, outcomes, pricing, ingest, dashboard, evals, model config |

## Quickstart commands

```bash
# Setup (pytest is the only dev dep)
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# Run tests
.venv/bin/python -m pytest tests/ -q

# Backfill Claude Code sessions into the ledger
python3 telemetry.py ingest claude-code --all

# View ledger report
python3 telemetry.py report

# Run evals
python3 telemetry.py eval all

# Generate static dashboard
python3 telemetry.py dashboard                   # writes dashboard/index.html

# Launch interactive dashboard
python3 dashboard/server.py                      # open http://127.0.0.1:7799

# Route (and optionally execute) a task
python3 llmops.py --task "add a retry to the download path" --run

# Run the business savings model
python3 calculator/savings_model.py
```

Windows: use `.venv\Scripts\python.exe`; use `python3.13` explicitly if your
`python` is older than 3.9.

## Section index

- [Architecture](architecture/overview.md) — How `ModelRouter`, `CostMonitor`, `CodingMemory`, the telemetry ledger, evals, dashboard, and calculator connect.
- [Workflows](workflows/overview.md) — The end-to-end loop: ingest → route → execute → grade → evaluate → tune.
- [Domain Concepts](domain-concepts/overview.md) — Complexity tiers, the hybrid classifier, consequence-based CRITICAL severity, outcome grading.
- [Data Models](data-models/overview.md) — Ledger event schemas, idempotency/dedup, imputed vs actual cost.
- [Operations](operations/overview.md) — CLI reference, env vars, llama-swap local inference topology.
- [Integrations](integrations/overview.md) — Claude Code SessionEnd hook, llama-swap, headroom context-compression proxy.
- [Testing & Evals](testing-evals/overview.md) — The pytest suite and what each eval measures.

## Honest numbers disclaimer

The repo's measured numbers come from a 12-task live run on one box (RX 7600 XT
16 GB / 32 GB RAM, q4 quants). **Treat every number as small-N evidence, not
statistics.** Model-involved numbers wobble run to run (the 9B is
non-deterministic). All measurements are reproducible with the listed commands;
every calculator input carries a provenance tag.
