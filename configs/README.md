# configs/ — bring your own models

The router ships with the author's model map (a llama-swap box + opencode cloud
tiers). To run it with **your** models, point `LLMOPS_MODEL_CONFIG` at a JSON
file — no code edits:

```bash
LLMOPS_MODEL_CONFIG=configs/ollama.json python3 dashboard/server.py
```

## Shape

```json
{
  "rates":       { "<model-name>": {"input": 0.0, "output": 0.0} },
  "preferences": { "CRITICAL": ["<model-name>", "..."],
                   "COMPLEX":  ["..."],
                   "MODERATE": ["..."],
                   "SIMPLE":   ["..."] }
}
```

- `rates` — USD per **1M tokens**, merged over the built-in table. Local
  self-hosted models are `0.0` (electricity is accounted separately, in the
  calculator's infra line — honesty rule).
- `preferences` — per-tier model chains, first affordable candidate wins. A
  partial mapping updates only the tiers it names. Every model named here needs
  a rate; violations fail loudly at import, not silently at route time.
- **Naming convention:** models whose name starts with `llama-cpp/` are treated
  as local (the cost gate can force-route to them; executions log
  `cost_model="local"`). The name is the *accounting label* — which endpoint and
  key actually get called is set by env:

| Env var | Meaning |
|---|---|
| `LLMOPS_LOCAL_BASE_URL` | OpenAI-compatible endpoint for execution |
| `LLMOPS_LOCAL_MODEL` | model key to request there |
| `LLMOPS_CLASSIFIER_BASE_URL` | endpoint for the tier classifier (optional) |
| `LLMOPS_CLASSIFIER_MODEL` | classifier model key (optional) |

## Presets

| File | Setup |
|---|---|
| `ollama.json` | Ollama at `:11434`, big coder model for hard tiers, small model for SIMPLE |
| `lmstudio.json` | LM Studio local server at `:1234`, one loaded model |
| `local-only.json` | Any llama.cpp `llama-server`/llama-swap, one model, nothing cloud |

## Zero models at all?

Skip the config entirely. The classifier degrades to keyword-only, execution is
opt-in (leave it unchecked), and every dashboard pane still works — routing
decisions, the ledger, batch confusion matrices, the calculator. That is the
supported minimum for trying the project on any machine.
