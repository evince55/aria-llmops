# Workflows

The end-to-end loop: **ingest → route → execute → grade → evaluate → tune**.

Each step has at least one CLI command. The loop is designed to run
reproducibly; the README's measured numbers come from running it end-to-end on
2026-07-09.

## 1. Ingest — fill the ledger

Claude Code sessions are the primary source. Each session produces a JSONL
transcript in `~/.claude/projects/<project>/`. The ingest pipeline parses these
into normalized `usage` events.

```bash
# Ingest all sessions for the default project
python3 telemetry.py ingest claude-code --all

# Ingest a single session
python3 telemetry.py ingest claude-code --session path/to/transcript.jsonl

# Override project directory
python3 telemetry.py ingest claude-code --project-dir ~/other-project
```

**What happens:**
1. `telemetry/ingest_claude_code.py` scans the project dir for `*.jsonl` files
2. Each transcript is parsed: session ID, first user message (→ `task_text`),
   per-message token counts, git context (branch resolution from worktree path)
3. `make_usage_event()` builds normalized events with `imputed_usd` from
   `pricing.imputed_usd()`
4. `schema.append_events()` writes to `telemetry/events.jsonl`, skipping
   duplicates by `harness|session_id|msg_id`

**Key files:** `telemetry/ingest_claude_code.py`, `telemetry/schema.py`,
`telemetry/pricing.py`

For Claude Code users, the SessionEnd hook (`telemetry/hooks/claude_code_session_end.py`)
auto-ingests each session on close — no manual `--all` needed.

## 2. Route — classify and pick the model

```bash
# Classify and route (no execution)
python3 llmops.py --task "add a retry to the download path"

# Use the 9B model classifier instead of keyword
python3 llmops.py --task "..." --classifier model

# Specify estimated token count
python3 llmops.py --task "..." --tokens 1500
```

**What happens:**
1. `ModelRouter.classify_hybrid()` runs: keyword patterns first, 9B model
   rescue if keyword defaulted
2. `CostMonitor.should_route_to_local()` checks rolling-window budgets
3. Router picks the first candidate from `TIER_PREFERENCE[complexity]` whose
   cost doesn't trigger the gate
4. A `route_decision` event is written to the ledger (if `log_decisions=True`)

The output is JSON with `model`, `reason`, `estimated_cost`, `complexity`,
`alternatives`, and `similar_solutions`.

**Key files:** `llmops.py` ModelRouter, CostMonitor

## 3. Execute — run on local model

```bash
python3 llmops.py --task "write a function to validate email" --run
python3 llmops.py --task "..." --run --max-tokens 512
```

**What happens:**
1. `ModelRouter.run_task()` calls `route_task()` first
2. If the chosen model starts with `llama-cpp`, it executes via
   `LocalLlamaClient.complete()` (urllib POST to the llama-swap endpoint)
3. A `usage` event is written with actual token counts and `imputed_usd=$0`
4. Cloud/frontier tiers are decided but not executed from this process

The dashboard's Runner pane (`runner.py`) wraps this same flow: route →
optionally execute → grade → capture.

## 4. Grade — infer session outcomes

Outcomes are inferred from Claude Code transcript user reactions:

```bash
# Dry-run: see what outcomes would be assigned
python3 telemetry.py backfill-outcomes

# Actually stamp outcomes onto ledger events
python3 telemetry.py backfill-outcomes --write

# Include the 9B model grader for inconclusive sessions
python3 telemetry.py backfill-outcomes --write --grade-with-model
```

**What happens:**
1. `outcomes.py:outcome_from_transcript()` extracts user messages from each
   parsed transcript
2. Keyword heuristic (`outcome_from_user_texts()`): scans for
   success/failure phrases, with negation guards
3. Last decisive signal wins — an early complaint fixed later = success
4. If keyword is inconclusive AND `--grade-with-model` is passed, the 9B
   model grader (`grade_outcome()`) reads the reaction turns holistically
5. Result: `"success"`, `"failure"`, or `None` (deliberately no guess)

Outcomes are high-precision only — unlabeled sessions are never assumed good
or bad.

**Key files:** `telemetry/outcomes.py`

## 5. Evaluate — measure accuracy, efficiency, quality

```bash
# Run all evals
python3 telemetry.py eval all

# Individual evals
python3 telemetry.py eval classification
python3 telemetry.py eval efficiency
python3 telemetry.py eval quality

# Use 9B for quality eval classification (catches prose tasks keyword misses)
python3 telemetry.py eval quality --model-classifier

# Live A/B run (requires local inference)
python3 evals/live_routing_ab.py run
python3 evals/live_routing_ab.py grade --reactions evals/live-runs/reactions.json
python3 evals/live_routing_ab.py report
```

**What happens:**
- **Classification eval** scores `classify()` against labeled datasets, reports
  accuracy + per-tier precision/recall + confusion matrix
- **Efficiency eval** replays ledger sessions through the router, reports
  `local_first_sessions_pct` and complexity distribution
- **Quality eval** joins outcomes onto spend: `cheap_routing_failures` (did
  cheap routing hurt?) and `downgrade_candidates` (where is frontier spend
  unjustified?)
- **Live A/B** runs real tasks through the hybrid classifier (Arm A) and
  9B-primary (Arm B), executes Arm A on the 35B, then grades + summarizes

See [Testing & Evals](testing-evals/overview.md) for the full eval catalog.

**Key files:** `evals/`, `telemetry.py` `_cmd_eval()`

## 6. Tune — use results to improve

The eval outputs suggest concrete tuning actions:

- `telemetry.py suggest` surfaces keyword-classifier mismatches vs the labeled
  set — direct candidates for keyword tuning
- Quality eval's `downgrade_candidates` are high-upside, low-risk: sessions
  that succeeded on frontier and could be tried on cheaper tiers
- Quality eval's `cheap_routing_failures` are regressions attributable to
  cheaper routing
- Dashboard Runner captures labeled examples to grow the classifier's
  training data
- **Dashboard Batch Runner** — route a whole labeled dataset through the
  classifier in one shot (Dashboard → Batch pane). The confusion matrix and
  per-task accuracy directly surface which tiers the classifier confuses,
  giving concrete targets for keyword tuning without running the full eval
  suite.

The loop feeds itself: every run grows the ledger, which feeds the evals,
which surface tuning candidates, which improve routing.
