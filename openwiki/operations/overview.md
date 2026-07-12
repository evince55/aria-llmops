# Operations

How to run, configure, and operate aria-llmops. Everything is stdlib-only
(Python 3.9+).

## CLI reference

### `llmops.py` — routing, execution, memory

| Command | What it does |
|---|---|
| `python3 llmops.py --task "..."` | Classify and route a task (no execution); prints JSON decision |
| `python3 llmops.py --task "..." --run` | Route and execute on local model if routed to `llama-cpp/*` |
| `python3 llmops.py --task "..." --classifier model` | Use 9B model classifier instead of keyword |
| `python3 llmops.py --task "..." --tokens 1500` | Specify estimated token count (default 1000) |
| `python3 llmops.py --task "..." --run --max-tokens 512` | Cap output tokens for local execution (default 800) |
| `python3 llmops.py --store --problem "..." --solution "..." --cost 0.05` | Write a solved problem to coding memory |
| `python3 llmops.py --store --problem "..." --solution "..." --pattern-type refactor --category view` | Store with metadata |
| `python3 llmops.py --report` | Print cost report (limits, utilization, metrics) |
| `python3 llmops.py --report --by-area` | Include breakdown by `--category` |
| `python3 llmops.py --report --by-pattern` | Include breakdown by `--pattern-type` |

**Source:** `llmops.py` lines 886–978

### `telemetry.py` — ledger management and evals

| Command | What it does |
|---|---|
| `python3 telemetry.py ingest claude-code --all` | Parse all project transcripts into usage events |
| `python3 telemetry.py ingest claude-code --session path.jsonl` | Ingest a single transcript |
| `python3 telemetry.py report` | Ledger totals + by-outcome breakdown |
| `python3 telemetry.py eval classification` | Accuracy vs labeled dataset |
| `python3 telemetry.py eval efficiency` | Router replay on ledger sessions |
| `python3 telemetry.py eval quality` | Outcome-joined cost analysis |
| `python3 telemetry.py eval quality --model-classifier` | Use 9B for classification in quality eval |
| `python3 telemetry.py eval all` | Run all three evals |
| `python3 telemetry.py dashboard` | Write `dashboard/index.html` (static, self-contained) |
| `python3 telemetry.py dashboard --out custom.html` | Custom output path |
| `python3 telemetry.py suggest` | Keyword-classifier mismatches vs labeled set |
| `python3 telemetry.py reprice` | Dry-run: see old vs new imputed_usd totals |
| `python3 telemetry.py reprice --write` | Atomically rewrite ledger at current rates |
| `python3 telemetry.py backfill-outcomes` | Dry-run: derive outcomes from transcripts |
| `python3 telemetry.py backfill-outcomes --write` | Stamp outcomes onto ledger events |
| `python3 telemetry.py backfill-outcomes --write --grade-with-model` | Include 9B grader for inconclusive |

All commands accept `--ledger <path>` to override the default
`telemetry/events.jsonl`.

**Source:** `telemetry.py` lines 1–220

### `calculator/savings_model.py`

```bash
python3 calculator/savings_model.py           # human-readable output
python3 calculator/savings_model.py --json    # machine-readable JSON
python3 calculator/savings_model.py --help    # all overridable params
```

### Live A/B harness

```bash
python3 evals/live_routing_ab.py run          # arms A+B; writes records
python3 evals/live_routing_ab.py grade \
  --reactions evals/live-runs/reactions.json  # grade from reviewer reactions
python3 evals/live_routing_ab.py report       # print final results
```

### Dashboard

```bash
python3 dashboard/server.py                   # interactive (http://127.0.0.1:7799)
python3 telemetry.py dashboard                # static HTML (dashboard/index.html)
```

#### Interactive API endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/api/overview` | Ledger totals, by-model spend, tier distribution, efficiency snapshot |
| GET | `/api/events` | Recent `usage` events (tail; `?limit=50`) |
| GET | `/api/runs` | Recent Runner events (harness=`dashboard-runner`; `?limit=25`) |
| POST | `/api/run` | Route (optionally execute) a task, hold in pending; body: `{task, execute}` |
| POST | `/api/run/outcome` | Grade a pending run; body: `{run_id, outcome}` |
| POST | `/api/dataset/capture` | Capture a labeled example to the classifier dataset |
| GET | `/api/ledger` | **Ledger Explorer** — faceted, filtered, paged view of the raw ledger; `?event=&harness=&model=&outcome=&q=&limit=&offset=` |
| GET | `/api/datasets` | **Batch Runner** — list labeled datasets in `evals/datasets/*.jsonl` with counts |
| POST | `/api/batch` | **Batch Runner** — route a whole dataset; body: `{dataset, log}`; returns rows, confusion matrix, accuracy |
| GET | `/api/calculator` | Savings calculator with override params |
| GET | `/api/classifier-status` | Latest classifier accuracy across all labeled datasets (shown as an Overview card) |

## Environment variables

### Inference configuration

| Variable | Default | Description |
|---|---|---|
| `LLMOPS_INFERENCE_MODE` | `swap` | `swap` = single llama-swap endpoint; `dual` = legacy two-port layout |
| `LLMOPS_SWAP_ENDPOINT` | `http://localhost:8080/v1` | The one endpoint (swap mode) |
| `LLMOPS_LOCAL_BASE_URL` | (derived) | Overrides executor URL in any mode |
| `LLMOPS_LOCAL_MODEL` | `qwen3.6-35b` | Executor model key (swap mode) or gguf filename (dual mode) |
| `LLMOPS_CLASSIFIER_BASE_URL` | (derived) | Overrides classifier URL |
| `LLMOPS_CLASSIFIER_MODEL` | `9b-mythos` | Classifier model key (swap mode) or gguf filename (dual mode) |
| `LLMOPS_LOCAL_THINKING` | `0` | Set to `1` to enable Qwen 3.6 reasoning mode |
| `LLMOPS_MODEL_CALL_TIMEOUT` | `45` | Seconds for classify/grade calls — must cover llama-swap swap-in (~14s measured) |

The default topology is **swap**: one llama-swap endpoint at
`http://localhost:8080/v1` fronts both models. The executor is `qwen3.6-35b`
(35B MoE), the classifier is `9b-mythos` (9B). In swap mode, `LOCAL_BASE_URL`
= `CLASSIFIER_BASE_URL` = the same endpoint; only the model keys differ. This
is the live, smoke-tested layout (verified 2026-07-09).

The **dual** topology (legacy) uses two separate llama.cpp servers on different
ports (35B on `:8080`, 9B on `:8081` by LAN IP). Nothing listens on `:8081` in
the current deployment.

Any explicit env var overrides the mode-derived default, so custom topologies
are a pure env-var change.

**Source:** `llmops.py` `resolve_inference_config()` (lines 180–205)

### Cost and budget

| Variable | Default | Description |
|---|---|---|
| `LLMOPS_5HR_USD` | `12` | 5-hour rolling cap |
| `LLMOPS_WEEKLY_USD` | `30` | 7-day rolling cap |
| `LLMOPS_MONTHLY_USD` | `60` | 30-day rolling cap |

The 80% force-local threshold is hardcoded in `TierLimits`.

### Dashboard

| Variable | Default | Description |
|---|---|---|
| `ARIA_DASH_PORT` | `7799` | Dashboard server port |
| `ARIA_DASH_HOST` | `127.0.0.1` | Set `0.0.0.0` to reach it over LAN/Tailscale |

### Runner (dashboard data-gen)

| Variable | Default | Description |
|---|---|---|
| `RUNNER_MAX_TOKENS` | `512` | Max output tokens for local execution in Runner pane |

### Other

| Variable | Default | Description |
|---|---|---|
| `LLMOPS_LOG_LEVEL` | `INFO` | Python logging level for llmops module |
| `LLMOPS_LEDGER` | (schema default) | Override ledger path for ALL telemetry (CLIs, dashboard, runner, hook) — read at import |
| `LLMOPS_MODEL_CONFIG` | (none) | Path to a JSON config that maps your own models into the tiers (see `configs/` for presets: ollama, LM Studio, llama-server) |

## Local inference setup

The router talks to a local llama.cpp server via its OpenAI-compatible API.
In the production (swap) topology:

1. **llama-swap** runs on `localhost:8080`, configured with two model keys:
   `qwen3.6-35b` (Qwen 3.6 35B MoE, q4 quant, executor) and `9b-mythos` (9B
   classifier model, q8 quant).

2. Llama-swap loads models on demand — they may not be co-resident. Requesting
   the other model may trigger a swap (~14s measured for 35B→9B on the dev
   box). This is why `LLMOPS_MODEL_CALL_TIMEOUT` defaults to 45s: it must
   cover the measured swap-in plus the actual inference.

3. The 35B is a *reasoning* model whose default behavior spends the entire
   token budget in the reasoning channel and returns empty content.
   `enable_thinking=false` is hardcoded in `LocalLlamaClient._build_body()` and
   is **load-bearing** — without it, local execution produces no output.

4. Verify the endpoint: `curl http://localhost:8080/v1/models` should list
   both model keys.

**Source:** `llmops.py` `LocalLlamaClient` (lines 235–275), `resolve_inference_config()`
(lines 180–205), llama-swap constants (lines 166–177)

## Ledger lifecycle

- `telemetry/events.jsonl` is gitignored — it contains local data
- Append-only, never truncated (events accumulate)
- `reprice --write` rewrites atomically (temp file + rename)
- `backfill-outcomes --write` rewrites atomically
- Event constructors enforce `TASK_TEXT_MAX=500` at the schema level
