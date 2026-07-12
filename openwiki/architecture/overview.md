# Architecture

How the pieces of aria-llmops connect. Every component is stdlib-only (Python
3.9+).

## Component map

```
                                  telemetry/events.jsonl
                                  (append-only JSONL ledger)
                                         |
        ┌──────────┬──────────┬───────────┼───────────┬──────────┐
        v          v          v           v           v          v
   ingest_claude  report    evals/    dashboard/   reprice   backfill-
   _code.py      (by model,  (class.,   (server.py,  .py       outcomes
   (transcript   by outcome)  eff.,     generate.py,           (outcomes.py)
    parser)                   quality)  runner.py,
                                        web/ static)

  llmops.py
  ├── ModelRouter        ── classify (keyword) / classify_via_model (9B) /
  │                          classify_hybrid (keyword-first + 9B-rescue)
  ├── CostMonitor        ── rolling 5h/7d/30d spend gates, 80% threshold
  ├── CodingMemory       ── persistent problem→solution store with
  │                          token-overlap similarity, rolling spend
  ├── LocalLlamaClient   ── urllib POST to OpenAI-compatible endpoint
  ├── ModelClassifier    ── 9B tier classification with keyword fallback
  └── CLI                ── --task, --run, --store, --report

  calculator/savings_model.py
    ── Three-world comparison: human_baseline, naive_ai, routed_ai
    ── Provenance tags on every input
    ── The "honesty block" in output
```

## ModelRouter (`llmops.py`)

The central piece. Three classification strategies, all on one class:

| Method | Strategy | Source | When used |
|---|---|---|---|
| `classify()` | Keyword-only | `COMPLEXITY_KEYWORDS` dict | CLI default, most evals |
| `classify_via_model()` | 9B-primary, keyword fallback | 9B via `ModelClassifier` | Downgrade audit (quality eval) |
| `classify_hybrid()` | Keyword-first, 9B-rescue on default | Both | **Production routing** |

All three check tiers in priority order: CRITICAL, COMPLEX, SIMPLE (checked
before MODERATE so narrow, high-precision rules aren't shadowed). MODERATE
requires ≥2 distinct keyword hits before being trusted — a single hit means the
classifier *defaulted*, not that it confidently identified MODERATE.

`route_task()` uses the result to pick the cheapest viable model from
`TIER_PREFERENCE`, gated by `CostMonitor.should_route_to_local()`. If the cost
gate blocks every cloud candidate, it falls back to the first local model in
the chain, or the cheapest overall if none is local.

`run_task()` extends this: if the chosen model is local (`llama-cpp/*`), it
executes on the live 35B and logs both a `route_decision` and a `usage` event.
Cloud tiers are decided but never executed from this process.

**Source:** `llmops.py` lines 618–884

## CostMonitor (`llmops.py`)

Tracks rolling-window spend against three caps:

| Window | Default cap | Env var |
|---|---|---|
| 5 hours | $12 | `LLMOPS_5HR_USD` |
| 7 days | $30 | `LLMOPS_WEEKLY_USD` |
| 30 days | $60 | `LLMOPS_MONTHLY_USD` |

If any window's spend reaches ≥80% of its cap (including the estimated cost of
the task being routed), `should_route_to_local()` returns `True` and the router
downgrades to local. The 80% threshold is hardcoded in `TierLimits`.

Spend is tracked via `CodingMemory.spend_since()`, which sums stored entry
costs within each rolling window — so old spend ages out (unlike the original
implementation, which compared lifetime `total_spent` and permanently forced
local routing once a cap was crossed).

**Source:** `llmops.py` lines 508–616

## CodingMemory (`llmops.py`)

A persistent JSON store (`.coding_memory.json`) of solved problems with:

- **Deduplication** via SHA-256 hash of normalized problem text
- **Similarity search** via Jaccard token overlap (O(n) scan; fine for the
  expected handful-of-MB store)
- **Rolling spend** via `spend_since(seconds)` — the engine behind
  CostMonitor's windows
- **Aggregation** via `by_category()` and `by_pattern()` for the `--report`
  breakdowns
- **Atomic writes** via temp-file-and-rename

The CLI surface: `llmops.py --store --problem "..." --solution "..." --cost
0.05` writes an entry; `llmops.py --report [--by-area] [--by-pattern]` prints
the aggregated view.

**Source:** `llmops.py` lines 330–506

## Telemetry ledger (`telemetry/`)

An append-only, idempotent JSONL file (`telemetry/events.jsonl`, gitignored).
Two event types:

### `usage` events
Created by `telemetry/schema.py:make_usage_event()`. Fields:
`harness`, `session_id`, `msg_id`, `model`, `input_tokens`, `output_tokens`,
`cache_write_tokens`, `cache_read_tokens`, `cost_model`, `actual_usd`,
`imputed_usd`, `cwd`, `git_branch`, `task_text` (capped at 500 chars), `outcome`.

### `route_decision` events
Created by `telemetry/schema.py:make_route_decision_event()`. Fields:
`harness`, `task_text` (capped), `complexity`, `chosen_model`, `estimated_usd`,
`alternatives` (list of {model, estimated_cost}).

### Idempotency
`dedup_key()` computes `harness|session_id|msg_id` for usage events.
`append_events()` loads existing keys, skips duplicates. `route_decision`
events have no dedup key — they always append.

### Pricing
`telemetry/pricing.py` holds per-model USD/1M-token rates and `imputed_usd()`:
given a model name and token counts, computes the list-rate cost. Covers Claude
models (Opus, Fable, Sonnet, Haiku), opencode models (minimax-m3,
deepseek-v4-flash, qwen3.7-plus), and local (`llama-cpp/*` = $0).

**Source:** `telemetry/schema.py`, `telemetry/pricing.py`

## Evals (`evals/`)

Offline evaluation suite that turns the ledger + labeled datasets into numbers:

| Eval | What it measures |
|---|---|
| `router_classification_eval.py` | Classifier accuracy vs labeled tasks |
| `classifier_comparison.py` | Keyword vs 9B-primary vs hybrid on both labeled sets |
| `routing_efficiency_eval.py` | What the router *would* pick vs what actually ran |
| `routing_quality_eval.py` | Joins outcomes onto spend: cheap-routing failures, downgrade candidates |
| `live_routing_ab.py` | Live A/B harness: route + execute + grade, production-style |

Datasets live in `evals/datasets/`: `labeled_tasks.jsonl` (keyword-tuned),
`labeled_tasks_prose.jsonl` (keyword-blind), `labeled_tasks_balanced.jsonl`,
`labeled_severity.jsonl`.

See [Testing & Evals](testing-evals/overview.md) for what each eval measures
(and does NOT measure).

## Dashboard (`dashboard/`)

Two forms:

1. **Interactive** (`server.py`): stdlib `http.server` on port 7799, serves
   `dashboard/web/` (vanilla JS, no CDN) plus JSON endpoints for telemetry,
   router, classifier, calculator, and live-run data. Eight panes: Overview,
   Router, Runner, **Ledger** (Explorer), **Batch** (Batch Runner), Classifier,
   Calculator, Live Run.

   - **Runner** — submit tasks, optionally execute on the local 35B, grade
     outcomes, and capture labeled examples. Endpoints: `POST /api/run`,
     `POST /api/run/outcome`, `POST /api/dataset/capture`, `GET /api/runs`.
   - **Ledger Explorer** — faceted, filterable, paged view of the raw telemetry
     ledger. Filter by event type, harness, model, outcome, and task-text
     search. Returns flattened rows (unified shape for `usage` and
     `route_decision` events), facet counts, and a summary with totals.
     Uses `_norm_event()` to harmonize the two event schemas.
     Endpoint: `GET /api/ledger?event=&harness=&model=&outcome=&q=&limit=&offset=`
   - **Batch Runner** — route a whole labeled dataset through the classifier
     in one shot. Lists available datasets (`GET /api/datasets`), then POSTs
     to `POST /api/batch` (body: `{dataset, log}`) which returns per-task
     results, a confusion matrix, accuracy vs expected labels, and tier
     distribution. Logged events use harness `dashboard-batch`. Max 200
     tasks per batch (`BATCH_MAX`).

2. **Static** (`generate.py`): produces a self-contained HTML file with
   inline-SVG bar charts and embedded data. No server required.

**Source:** `dashboard/server.py`, `dashboard/runner.py`, `dashboard/generate.py`,
`dashboard/web/`

## Savings calculator (`calculator/`)

`calculator/savings_model.py` compares three worlds on a monthly basis for a
given task stream:

1. **human_baseline** — people do everything
2. **naive_ai** — frontier model for everything automatable
3. **routed_ai** — the offering: classify each task, run on cheapest capable tier

Every input carries a provenance tag (measured / list-rate / assumption). The
model recommends whichever of local-box vs cloud-only is cheaper at the given
volume; its own measured break-even is ~234k tasks/month. Below that, the
calculator recommends cloud-only and says the box is a privacy/rate-limit play,
not a cost play.

Defaults seeded from the live run: 0.727 local success rate, 2,000 tasks/mo.

**Source:** `calculator/savings_model.py`
