# aria-llmops — Wiki brief (generation guidance)

Audience: a new engineer onboarding to this repo. Be accurate, concrete, and tight;
preserve the repo's honest tone (measured numbers are small-N evidence, not
statistics — never inflate).

## Produce a COMPLETE multi-page wiki
`quickstart.md` (overview + "start here" navigation + key source files) plus focused
section pages covering:
- **architecture** — how the pieces connect: `ModelRouter` + `CostMonitor` +
  `CodingMemory` (llmops.py); the telemetry ledger (telemetry/, one append-only JSONL
  of route_decision + usage events); the evals suite (evals/); the dashboard
  (dashboard/ — stdlib http.server + vanilla JS, with the Runner/Batch/Ledger
  data-gen panes); the savings calculator (calculator/).
- **workflows** — ingest -> route -> (optionally execute) -> grade -> evaluate -> tune.
- **domain concepts** — complexity tiers SIMPLE/MODERATE/COMPLEX/CRITICAL; the
  keyword-first + 9B-rescue hybrid classifier; consequence-based CRITICAL severity;
  outcome grading.
- **data models** — the ledger event schema (usage vs route_decision fields),
  idempotency/dedup, imputed vs actual cost.
- **operations** — the CLIs (`llmops.py` routing/exec, `telemetry.py`
  ingest/report/eval/dashboard), env vars, and local inference via llama-swap (one
  OpenAI-compatible endpoint; executor + classifier models).
- **integrations** — the Claude Code SessionEnd ingest hook, llama-swap, the
  headroom context-compression proxy.
- **testing & evals** — the pytest suite and what each eval (classification,
  efficiency, quality, live A/B) measures.

## Hard rules
- **CRITICAL: never link to a page you do not actually create.** Every link must
  resolve to a file you write in this run. If you plan a page, write it.
- The runtime is **stdlib-only** (Python 3.9+, zero third-party runtime deps);
  pytest is the only dev dependency. Say so where relevant.

## Where to look
Key files: `llmops.py`, `telemetry.py`, `telemetry/schema.py`, `telemetry/pricing.py`,
`evals/`, `dashboard/server.py`, `dashboard/runner.py`, `calculator/savings_model.py`,
`README.md`, `docs/`.
