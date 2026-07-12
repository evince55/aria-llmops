# Data Models

The telemetry ledger (`telemetry/events.jsonl`) is the central data store. It
is an append-only, idempotent JSONL file, gitignored. Every line is one JSON
object with an `"event"` discriminator.

## Event types

### `usage` event

Created by `telemetry/schema.py:make_usage_event()`. Records a single model
call (Claude Code assistant message, or local execution).

| Field | Type | Description |
|---|---|---|
| `event` | str | Always `"usage"` |
| `ts` | str | ISO 8601 UTC timestamp |
| `harness` | str | Source: `"claude-code"`, `"llmops-local"`, `"dashboard-runner"`, `"llmops-live"` |
| `session_id` | str | Session identifier (Claude Code sessionId, or generated UUID) |
| `msg_id` | str | Per-message identifier for dedup |
| `model` | str | Model name (e.g., `"claude-sonnet-5"`, `"llama-cpp/qwen35b"`) |
| `input_tokens` | int | Prompt tokens |
| `output_tokens` | int | Completion tokens |
| `cache_write_tokens` | int | Cache write tokens (Claude models) |
| `cache_read_tokens` | int | Cache read tokens (Claude models) |
| `cost_model` | str | `"subscription"`, `"api"`, or `"local"` |
| `actual_usd` | float | Actual billed cost (always $0 for subscription/local) |
| `imputed_usd` | float | List-rate cost computed from token counts |
| `cwd` | str \| null | Working directory at time of call |
| `git_branch` | str \| null | Resolved branch name (derived from worktree path for detached HEAD) |
| `task_text` | str \| null | First user message of the session, capped at 500 chars |
| `outcome` | str \| null | `"success"`, `"failure"`, or `null` (unlabeled) |

**Source:** `telemetry/schema.py` lines 29–66

### `route_decision` event

Created by `telemetry/schema.py:make_route_decision_event()`. Records a router
decision — no actual model call occurred.

| Field | Type | Description |
|---|---|---|
| `event` | str | Always `"route_decision"` |
| `ts` | str | ISO 8601 UTC timestamp |
| `harness` | str | Source: `"opencode"`, `"llmops"`, `"dashboard-runner"`, `"llmops-live"`, `"llmops-ab-9bprimary"` |
| `task_text` | str | The routed task, capped at 500 chars |
| `complexity` | str | Classified tier: `"SIMPLE"`, `"MODERATE"`, `"COMPLEX"`, `"CRITICAL"` |
| `chosen_model` | str | The selected model |
| `estimated_usd` | float | Estimated cost for this task |
| `alternatives` | list | Array of `{"model": str, "estimated_cost": float}` for all candidates |

**Source:** `telemetry/schema.py` lines 69–88

## Idempotency and dedup

The ledger uses `dedup_key()` to prevent duplicate entries:

- **Usage events:** keyed by `harness|session_id|msg_id`. When ingesting the
  same transcript twice, only new messages are appended.
- **Route_decision events:** no dedup key — they always append.

`schema.append_events()` loads all existing keys from the ledger file (O(n)
startup scan), then skips any incoming event whose key already exists.
`schema.read_events()` reads all events; it handles the case where the ledger
doesn't exist yet (returns `[]`).

**Source:** `telemetry/schema.py` lines 91–130

## Task text cap

All event constructors clip `task_text` to `TASK_TEXT_MAX = 500` characters.
This is enforced in the constructors (not by callers) to prevent ledger bloat.
The cap was introduced after a multi-page routed prompt wrote its full text
(~20,000 chars) into every route_decision.

**Source:** `telemetry/schema.py` lines 16–22

## Imputed vs actual cost

- **`imputed_usd`**: what the call *would* cost at published list API rates.
  Computed by `telemetry/pricing.py:imputed_usd()` from stored token counts ×
  per-model rates. This is the number used for cost analysis, evals, and the
  calculator. It is *stamped at ingest time* with the rates current at that
  moment.

- **`actual_usd`**: what was actually billed. Always $0 for subscription models
  (Claude Code Max plan) and local models. Currently always $0 in practice.

- **Repricing:** `telemetry.py reprice [--write]` recomputes `imputed_usd` for
  every usage event using *current* rates from `pricing.py`. This corrects old
  events when rates change (e.g., the Opus 4.8 $15/$75 → $5/$25 correction).
  Dry-run by default; `--write` atomically rewrites the ledger.

**Source:** `telemetry/pricing.py`, `telemetry/reprice.py`

## Pricing rates

Model prices are defined in two places that must stay in sync:

1. **`llmops.py` `MODEL_RATES`** — used by `CostMonitor.estimate_cost()` for
   routing decisions. Covers opencode/local models.

2. **`telemetry/pricing.py` `PRICING`** — used by `imputed_usd()` for ledger
   events. Covers Claude + opencode/local models. This is the single source of
   truth for ledger accounting.

Both use the same dollar-per-million-token format. Adding a new model means
adding entries to both dicts.

**Source:** `llmops.py` lines 56–62, `telemetry/pricing.py` lines 13–27
