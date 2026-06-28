# LLMOps Telemetry, Evals & Observability — Design

**Date:** 2026-06-28
**Status:** Draft for review
**Author:** Claude Code (with owner)
**Location of work:** `tools/llmops/` (workspace root; see *Open Items → Version control*)

## 1. Context & goal

`tools/llmops/llmops.py` already has the routing primitives (`ModelRouter`,
`CostMonitor`, `CodingMemory`) but **no live data**: cost is only recorded when an
agent manually runs `--store --cost`, the cost gate uses lifetime spend for all
tiers, and the only eval (`evals/headroom_*`) is unrelated to routing.

The owner is moving primary development from **opencode** (subscription ending)
to **Claude Code** (Max plan) for Aria audit tracks 2–5. The goal of this work
is to build the **logging / eval / observability infrastructure** so that those
future sessions generate real data, and the LLMOps workflow can be matured on
live data over time.

**Success criteria:**
1. Every model call from both harnesses lands in one normalized event ledger.
2. Existing Claude Code sessions are backfilled (real data on day one).
3. A reproducible eval suite measures router quality from that data.
4. A self-contained dashboard makes spend + routing visible.
5. The path to improve routing from accumulated data is in place.

## 2. Non-goals (YAGNI)

- No output **quality** judging this round (no manual tags, no LLM-judge). The
  schema reserves an optional `outcome` field for later; we do not populate it.
- No live-serving dashboard / web framework. Static HTML only.
- No heavy opencode transcript parsing (data is sparse on disk and the harness
  is winding down). opencode contributes **routing decisions**, not usage.
- No change to how Claude Code itself runs (we observe, we don't route it).

## 3. Architecture

A **spine** (event ledger + ingestion) with three **consumers** (evals,
dashboard, routing loop). Each unit is independently testable and communicates
only through the ledger's JSON schema.

```
 Claude Code transcripts ─┐
                          ├─▶ ingestion ─▶ events.jsonl ─▶ ┌─ eval suite
 opencode (llmops route) ─┘   (parsers)    (the ledger)    ├─ dashboard (static HTML)
                                                           └─ routing loop (tuning)
```

**Key idea that unifies all three consumers:** Claude Code isn't routed by
llmops, but we **replay** each real Claude Code task through `ModelRouter`
offline and compare the router's *predicted* tier/model/cost against Opus's
*actual* usage. That one move feeds the eval (was the classifier right?), the
dashboard (predicted-vs-actual, "subscription saved $X vs API"), and the routing
loop (real task→outcome data to tune the classifier).

## 4. Event ledger (the spine)

**File:** `tools/llmops/telemetry/events.jsonl` (append-only JSON Lines; one
event per line). Gitignored (it's data). Append-only + idempotent ingestion =
crash-safe and re-runnable.

**Two event types**, one flat schema (fields absent when N/A):

`usage` — one per assistant message, from a transcript parser:
```json
{
  "event": "usage",
  "ts": "2026-06-28T14:00:00Z",
  "harness": "claude-code",
  "session_id": "a4d43c86-...",
  "msg_id": "<assistant uuid or requestId>",
  "model": "claude-opus-4-8",
  "input_tokens": 16657,
  "output_tokens": 410,
  "cache_creation_tokens": 7404,
  "cache_read_tokens": 19134,
  "cost_model": "subscription",
  "actual_usd": 0.0,
  "imputed_usd": 0.7421,
  "cwd": "/Users/chait/MusicAppIOS",
  "git_branch": "feat/...",
  "task_text": "<first user message of the session, truncated>",
  "outcome": null
}
```

`route_decision` — one per llmops route, written by `llmops.py` at route time:
```json
{
  "event": "route_decision",
  "ts": "...",
  "harness": "opencode",
  "task_text": "...",
  "complexity": "COMPLEX",
  "chosen_model": "llama-cpp/qwen35b",
  "estimated_usd": 0.0,
  "alternatives": [{"model": "...", "estimated_cost": 0.0}]
}
```

**Idempotency:** `(harness, session_id, msg_id)` is the dedup key for `usage`
events; re-ingesting a session never double-counts. The ledger writer skips keys
it has already seen (tracked via a sidecar `events.index` set, rebuilt from the
ledger on load).

**Cost semantics:**
- `cost_model: "metered"` (opencode) → `actual_usd` computed from `MODEL_RATES`.
- `cost_model: "subscription"` (Claude Code on Max) → `actual_usd = 0`,
  `imputed_usd` = what it *would* cost at list API rates (incl. cache read/write
  pricing). Powers the "routing/subscription saved $X" narrative honestly.

## 5. Ingestion

### 5a. Claude Code transcript parser — `telemetry/ingest_claude_code.py`
- Reads `~/.claude/projects/<project>/<session>.jsonl`.
- For each `assistant` line: extract `message.model`, `message.usage.*`,
  top-level `uuid`/`requestId`, `timestamp`, `sessionId`, `cwd`, `gitBranch`.
- `task_text` = first `user` line's `message.content` (string or first text
  block), truncated to N chars.
- Compute `imputed_usd` from a pricing table (`telemetry/pricing.py`) that
  includes Claude models (Opus/Sonnet/Haiku, input/output/cache rates).
- Emit `usage` events through the idempotent ledger writer.
- **Modes:** `--all` (backfill every session under the project dir — runs over
  your 11 existing sessions now) and `--session <path>` (one file, for the hook).

### 5b. SessionEnd hook
- A small script `telemetry/hooks/claude_code_session_end.py` reads the hook
  JSON on stdin (`{session_id, transcript_path, cwd, ...}`) and calls the parser
  on `transcript_path`. **Always exits 0** (never disrupts a session); fast; no
  network.
- Wired via `~/.claude/settings.json` `hooks.SessionEnd` (configured during
  implementation using the `update-config` skill). The hook just triggers the
  same parser used for backfill — one ingestion path.

### 5c. opencode route decisions
- `ModelRouter.route_task` (or a thin wrapper) appends a `route_decision` event
  to the ledger. This is the existing call site; no new harness integration.
- opencode *usage* parsing is deferred (sparse data, winding down).

## 6. Consumers

### 6a. Eval suite — `evals/`
- **`router_classification_eval.py`** — a hand-labeled dataset
  `evals/datasets/labeled_tasks.jsonl` (`{task, expected_tier}`, ~30–50 rows
  seeded from real task texts in the ledger + synthetic edge cases). Runs
  `ModelRouter.classify` over it and reports accuracy, per-tier
  precision/recall, and a confusion matrix. This is the regression guard for the
  keyword classifier.
- **`routing_efficiency_eval.py`** — replays `usage` events through the router:
  for each real task, compare predicted tier/model/cost vs actual model +
  `imputed_usd`; report how often a cheaper tier would plausibly have sufficed
  and the total imputed spend vs a "route-everything-local" floor. Cost-only,
  fully automatic (no quality label).
- Each eval prints a JSON summary and a human table; both are pytest-tested with
  fixtures.

### 6b. Dashboard — `dashboard/generate.py`
- Stdlib-only generator: reads the ledger (+ eval JSON outputs) and emits one
  **self-contained** `dashboard/index.html` — data embedded, charts as inline
  SVG / vanilla JS, **no external CDN**, opens offline in any browser.
- Views: spend over time (actual vs imputed), tier/model mix, predicted-vs-actual
  per task, cost by `cwd`/branch, classifier accuracy snapshot.
- `python3 telemetry.py dashboard` regenerates it.

### 6c. Routing loop
- The replay output (predicted vs actual) + the optional `outcome` field form
  the labeled corpus for tuning `COMPLEXITY_KEYWORDS` / `TIER_PREFERENCE`.
- This session ships a `telemetry.py suggest` stub that surfaces the worst
  misclassifications (router said CRITICAL but task was a cheap one-shot, etc.)
  as tuning candidates. Actual keyword tuning happens iteratively during tracks
  2–5 on accumulated data — documented, not automated.

## 7. CLI surface

A single `tools/llmops/telemetry.py` with subcommands (stdlib `argparse`):
- `ingest claude-code [--all | --session PATH]`
- `ingest opencode` (route-decision replay / no-op placeholder)
- `eval [classification | efficiency | all]`
- `dashboard`
- `report` (text summary of the ledger)
- `suggest` (routing-tuning candidates)

`llmops.py` gains a small ledger-append call in `route_task` (guarded so its
"stdlib only / no side effects in dry-run" contract holds).

## 8. Module layout

```
tools/llmops/
  llmops.py                      # existing; + route_decision ledger append
  telemetry.py                   # new CLI entrypoint
  telemetry/
    __init__.py
    schema.py                    # Event dataclasses, validate, ledger read/append (idempotent)
    pricing.py                   # model rates incl. Claude; imputed_usd calc
    ingest_claude_code.py        # transcript parser (backfill + single-session)
    ingest_opencode.py           # route-decision helper / deferred usage
    hooks/claude_code_session_end.py
    events.jsonl                 # the ledger (gitignored)
  evals/
    router_classification_eval.py
    routing_efficiency_eval.py
    datasets/labeled_tasks.jsonl
    headroom_*                   # existing, untouched
  dashboard/
    generate.py
    index.html                   # generated (gitignored)
  tests/
    fixtures/sample_transcript.jsonl   # trimmed, sanitized real transcript
    test_schema.py
    test_ingest_claude_code.py
    test_pricing.py
    test_router_classification_eval.py
    test_routing_efficiency_eval.py
    test_dashboard.py
  docs/specs/2026-06-28-llmops-telemetry-evals-design.md   # this file
```

## 9. Testing strategy

- **TDD throughout** (pytest, the venv already has it). Write the failing test,
  watch it fail, implement.
- Fixtures: a trimmed, sanitized real Claude Code transcript (a few assistant +
  user lines) under `tests/fixtures/`. No network in any test.
- Coverage: schema validation + idempotent dedup; parser token/cost extraction;
  pricing math (incl. cache tokens); classification eval metrics on a tiny known
  set; efficiency eval replay on fixture events; dashboard generator produces
  valid self-contained HTML containing expected data points.

## 10. Phasing

**This session:** spine (schema + pricing + CC parser + backfill of existing
sessions + SessionEnd hook) → classification eval + dataset → routing-efficiency
eval → static dashboard → `suggest` stub. Tests green throughout.

**Tracks 2–5 (ongoing, owner-driven):** data accumulates automatically; refine
the labeled dataset and `COMPLEXITY_KEYWORDS` from real misclassifications; add
the `outcome` signal (git-derived or manual) if/when wanted; revisit opencode
usage parsing only if opencode use resumes.

## 11. Open items

- **Version control.** `tools/llmops/` is currently **untracked** (workspace
  root, outside the Aria git repo) — the same reproducibility gap that made the
  backend awkward. Recommendation: `git init` a dedicated repo here (cleanest, it
  is its own product) OR vendor into the Aria repo under `tools/llmops/`. Owner
  to decide; this spec is written to disk now and committed once that's chosen.
- **Pricing accuracy.** Claude list rates (incl. cache read/write multipliers)
  to be filled from the current public price sheet at implementation time; the
  `claude-api` skill / docs are the source of truth.
- **Project scoping for backfill.** Default to the current project's transcript
  dir; `--all` could later span all projects.

## 12. Risks

- Claude Code transcript schema could change across versions → parser is
  defensive (tolerates missing fields, skips unparseable lines).
- Hook misconfiguration → kept trivial and exit-0; worst case it silently does
  nothing and backfill still works manually.
- "imputed" cost is an estimate, not a bill → always labeled as imputed and
  paired with `actual_usd` so it's never mistaken for real spend.
