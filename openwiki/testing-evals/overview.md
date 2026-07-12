# Testing & Evals

The pytest suite and the evaluation harness. All tests and evals are stdlib-only
except pytest (dev dependency only).

## Test suite (`tests/`)

**27 test files**, run with:

```bash
python3 -m pytest tests/ -q
```

### Test coverage by area

| Area | Test files | What they cover |
|---|---|---|
| **Router** | `test_router_fixes.py`, `test_router_hybrid_classify.py`, `test_model_classifier.py`, `test_route_logging.py` | Classification logic, hybrid strategy, model classifier, cost gate, SIMPLE reachability, MODERATE ≥2-hit rule, domain-noun filtering |
| **Schema** | `test_schema.py` | Event construction, idempotency, dedup |
| **Pricing** | `test_pricing.py`, `test_reprice.py` | imputed_usd calculation, reprice flow |
| **Ingest** | `test_ingest_claude_code.py`, `test_ingest_non_object_lines.py`, `test_hook.py` | Transcript parsing, non-dict-line defense, git branch resolution |
| **Outcomes** | `test_outcomes.py`, `test_outcome_grader.py`, `test_outcome_negation.py` | Keyword heuristic, model grader, negation guard |
| **Inference** | `test_inference_config.py`, `test_local_backend.py`, `test_model_call_timeout.py`, `test_task_text_cap.py` | Env var config resolution, llama client, timeout handling, ledger caps |
| **Evals** | `test_classification_eval.py`, `test_efficiency_eval.py`, `test_routing_quality_eval.py`, `test_quality_eval_full_text.py`, `test_severity_classification.py` | Eval correctness, edge cases |
| **Dashboard** | `test_dashboard.py`, `test_dashboard_honest_accuracy.py` | Dashboard data correctness, honest headline |
| **CLI** | `test_cli_commands.py`, `test_cli_ingest.py` | CLI arg parsing, ingest flow |
| **Savings** | `test_savings_model.py` | Calculator arithmetic |

### Philosophical notes

- **No mocking of external APIs:** the local inference tests use
  `resolve_inference_config(env={...})` to pin topology defaults, and timeouts
  are tested without actually calling a server.
- **Deterministic where possible:** keyword classifier tests are fully
  deterministic; model-based tests document non-determinism.
- **Schema-level caps:** `test_task_text_cap.py` verifies the 500-char
  enforcement in event constructors.

## Eval suite (`evals/`)

Each eval measures something specific — and explicitly documents what it does
NOT measure.

### Router classification eval (`evals/router_classification_eval.py`)

**What it measures:** Accuracy of `ModelRouter.classify()` against labeled
task→tier datasets. Reports overall accuracy, per-tier precision/recall,
confusion matrix.

**Datasets:**
- `labeled_tasks.jsonl` (n=24): keyword-tuned seed set — the keywords were
  written against it (self-fulfilling accuracy)
- `labeled_tasks_prose.jsonl` (n=18): keyword-blind — 8 rows from the real
  ledger, 10 authored
- `labeled_tasks_balanced.jsonl`: larger balanced set

**What it does NOT measure:** Real-traffic accuracy. The keyword-tuned set is
the tuning target.

Run: `python3 telemetry.py eval classification`

### Classifier comparison (`evals/classifier_comparison.py`)

**What it measures:** Keyword vs 9B-primary vs hybrid on both labeled sets +
union. Needs the 9B live; non-deterministic. Measured: keyword 71.4%,
9B-primary 81.0%, hybrid 83.3% on the union (n=42). The hybrid is ≥ both
parents on every dataset.

Run: `python3 evals/classifier_comparison.py`

### Routing efficiency eval (`evals/routing_efficiency_eval.py`)

**What it measures:** Replays ledger sessions through the router. Reports
`local_first_sessions_pct` (fraction of sessions whose router tier leads with
local) and complexity distribution.

**What it does NOT measure:** Output quality. `local_first_sessions_pct`
restates the `TIER_PREFERENCE` config as judged by the classifier — it is NOT
evidence that local models can actually do the work.

Run: `python3 telemetry.py eval efficiency`

### Routing quality eval (`evals/routing_quality_eval.py`)

**What it measures:** Joins outcomes onto spend to answer two questions:
1. **`cheap_routing_failures`** — labeled *failure* sessions that leaned on a
   non-frontier model. These are regressions attributable to cheaper routing.
   If empty, no observed failure is attributable to routing down.
2. **`downgrade_candidates`** / **`strong_downgrade_candidates`** — expensive
   *success* sessions that ran entirely on frontier, ranked by spend. Highest-
   upside, lowest-risk routing changes.

Unlabeled sessions are reported separately and NEVER assumed good or bad.
Outcomes are high-precision heuristic/model labels.

Run: `python3 telemetry.py eval quality [--model-classifier]`

### Live routing A/B (`evals/live_routing_ab.py`)

**What it measures:** The whole loop, live. Two arms:
- **Arm A (hybrid):** production keyword-first + 9B-rescue classifier, executes
  local-tier tasks on the 35B, logs route_decision + usage events
- **Arm B (9B-primary):** 9B-only classification, routed but NOT executed (avoids
  doubling 35B runs)

Then a human (or supervising agent) reviews outputs and authors reactions;
`grade` turns reactions into outcome labels and stamps them onto the ledger.
`report` summarizes with the quality eval.

**Default:** 12 tasks. N is small — evidence, not statistics.
Results from 2026-07-09: hybrid 10/12 tier accuracy, 8/11 local success (0.727).
Three failures analyzed: one genuine capability miss, one missed-the-ask, one
truncation casualty (7/11 outputs hit the 800-token cap — treat 0.727 as a
**floor** for a properly configured deployment).

Run: `python3 evals/live_routing_ab.py run | grade | report`

### Severity eval (`evals/severity_eval.py`)

**What it measures:** CRITICAL severity classification accuracy against
`labeled_severity.jsonl`. Verifies that consequence-signal keywords fire on
genuine severity without false positives on near-miss inputs.

### Headroom fidelity eval (`evals/headroom_fidelity_eval.py`)

**What it measures:** Whether context compression harms task completion
fidelity. Historical evidence for the headroom proxy decision. Requires the iOS
repo + a pinned third-party package (eval-only). Results committed in
`evals/headroom-results.json`.

### Embedding-based experiments (`evals/embedding_classifier.py`, `evals/embedding_comparison.py`)

Experimental classifiers using embeddings instead of keyword/LLM-based
classification. Not part of the production path.

### Classifier status (`evals/classifier_status.py`)

Reports the current state of keyword vs model classifier mismatches, using
`evals/results/classifier_status.json` as a snapshot reference.

## Test fixtures

`tests/fixtures/` contains shared test data. Tests construct fixtures
programmatically where possible (e.g., in-memory CodingMemory instances,
pinned env dicts for config resolution).

## Running everything

```bash
# Full test suite
python3 -m pytest tests/ -q

# With verbose output
python3 -m pytest tests/ -v

# Single test file
python3 -m pytest tests/test_router_fixes.py -v

# All evals (offline — classification + efficiency + quality)
python3 telemetry.py eval all

# Live A/B (requires llama-swap running)
python3 evals/live_routing_ab.py run
```
