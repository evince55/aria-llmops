# Integrations

External systems and tools that aria-llmops connects to.

## Claude Code SessionEnd hook

`telemetry/hooks/claude_code_session_end.py` is a Claude Code hook that
auto-ingests the just-finished session's transcript into the LLMOps ledger.
Configure it in Claude Code to run on session end.

**How it works:**
1. Receives JSON on stdin with key `transcript_path`
2. Calls `telemetry/ingest_claude_code.py:ingest()` on that single transcript
3. Writes usage events to the ledger (respects `LLMOPS_LEDGER` env var)
4. **Always exits 0** — telemetry failure must never disrupt a session

The hook is defensive: any exception is caught and swallowed. If the
transcript path is missing or doesn't exist, it silently returns.

**Setup:** Point Claude Code's hook configuration at this script. Use the
`LLMOPS_LEDGER` env var to override the ledger path from the hook's context.

**Source:** `telemetry/hooks/claude_code_session_end.py`

## llama-swap

[llama-swap](https://github.com/mostlygeek/llama-swap) is the model server
that fronts the local models. It exposes **one OpenAI-compatible endpoint**
that routes requests to different model backends based on the `model` field in
each request.

**Topology in production:**
- One endpoint: `http://localhost:8080/v1`
- Two model keys: `qwen3.6-35b` (executor, q4 quant) and `9b-mythos`
  (classifier, q8 quant)
- Models loaded on demand, not necessarily co-resident
- Swap-in time measured at ~14s (35B→9B) on the dev box (RX 7600 XT 16 GB /
  32 GB RAM)

**Configuration:** The router discovers the endpoint via
`LLMOPS_SWAP_ENDPOINT` (default `http://localhost:8080/v1`). In swap mode
(`LLMOPS_INFERENCE_MODE=swap`), both `LocalLlamaClient` instances (executor
and classifier) point at the same URL and differ only by model key.

**Verification:** `curl http://localhost:8080/v1/models` should return both
model keys.

**Source:** `llmops.py` lines 152–177, 208–232

## Headroom context-compression proxy

`headroom-proxy.sh` is a turnkey launcher for the Headroom
context-compression proxy — the savings engine of the LLMOps compression
layer. It transparently compresses large tool outputs (build logs, search
dumps) in the request path to reduce token consumption.

**Install:**
```bash
python3 -m venv ~/.headroom-venv
~/.headroom-venv/bin/pip install 'headroom-ai[mcp,proxy]==0.27.0'
```
Note: the package is `headroom-ai`, NOT `headroom` (namespace squatter).

**Usage:**
```bash
# Start on :8787 (token mode — max compression)
./headroom-proxy.sh

# Custom port
HEADROOM_PORT=8799 ./headroom-proxy.sh

# Cache mode (max prefix-cache hits)
HEADROOM_MODE=cache ./headroom-proxy.sh
```

**Routing clients through it:**
- Claude Code: `ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude`
- opencode / OpenAI-compatible providers: point the provider baseURL at
  `http://127.0.0.1:${PORT}/v1`
- Verify: `~/.headroom-venv/bin/headroom doctor`

The proxy pairs with the `headroom` MCP server (already registered in
`.opencode/opencode.jsonc`) so the model can retrieve compressed originals.

**The eval behind it:** `evals/headroom_fidelity_eval.py` measures whether
compressed context harms task completion fidelity. Results committed in
`evals/headroom-results.json` and documented in `evals/headroom-eval.md`. This
eval requires the iOS repo + a pinned third-party package (eval-only, not a
runtime dependency).

**Source:** `headroom-proxy.sh`, `evals/headroom_fidelity_eval.py`,
`evals/headroom-eval.md`

## Ingest sources (current and planned)

### Active: Claude Code transcripts
Parsed from `~/.claude/projects/<project>/*.jsonl`. The default project
directory is `-Users-chait-MusicAppIOS`. Each transcript is a JSONL file where
each line is a message event; `telemetry/ingest_claude_code.py` extracts token
counts, session IDs, task text, and git context.

### Planned: opencode
The CLI has `telemetry.py ingest opencode` stubbed. Currently returns
`{"ingested": 0}` with note "opencode usage parsing deferred; see
route_decision logging." Route decisions from `llmops.py --task` are already
logged under harness `"opencode"`.

### Planned: connector framework
The business roadmap (`docs/specs/2026-07-09-business-roadmap.md`) envisions
per-client connectors (email, CRM, sheets, ticket queue) feeding a shared
normalizer → router → executor pipeline. Only connectors + QA policy are
per-client; stages 2–6 (normalizer, router, executors, outcome grading,
ledger/reporting) are this repo.
