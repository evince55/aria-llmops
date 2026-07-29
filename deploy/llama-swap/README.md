# llama-swap deploy configs

## `nomic-embed.yaml` — embedding model for Aria library search

Registers the `nomic-embed` alias (nomic-embed-text-v1.5, 768-dim) that the
Aria backend's `/api/library/query` hybrid retrieval calls via OpenAI-format
`POST /v1/embeddings` (Aria repo, RAG Slice 2 PR).

### Owner runbook (on the homelab)

1. Download the GGUF into the models dir:

   ```bash
   cd "$LLAMA_MODELS_DIR"   # e.g. /home/eugene/models
   curl -LO https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q8_0.gguf
   ```

   (`nomic-embed-text-v1.5.f16.gguf` is the higher-fidelity alternative.)

2. Merge the `models."nomic-embed"` entry (plus macros, if your config does
   not already define them) into the config file the running llama-swap was
   started with — llama-swap loads a single config file. For a standalone
   smoke test, `nomic-embed.yaml` is valid on its own.

3. Restart llama-swap, then smoke-test through the fronting port (**:8080**,
   the same endpoint `LLMOPS_SWAP_ENDPOINT` defaults to):

   ```bash
   curl -s http://127.0.0.1:8080/v1/embeddings \
     -H 'Content-Type: application/json' \
     -d '{"model":"nomic-embed","input":["search_query: mellow guitar"]}' \
     | jq '.data[0].embedding | length'   # expect 768
   ```

4. Point the Aria backend at it: `ARIA_EMBED_URL=http://127.0.0.1:8080/v1/embeddings`
   (the updated `aria-backend.service` in the Aria repo sets exactly this).

### Not verified from the dev machine

- VRAM behaviour on the gfx1102 host (the Q8_0 weights are ~140 MB, so the
  risk is swap-eviction of the resident chat model, not fit — see the
  EVICTION WARNING in the yaml).
- Whether the deployed llama-swap build supports routing groups; the yaml
  works without them.

### Dimensions

Full 768-dim is served. The Aria RAG spec (§9) defers the 256-dim matryoshka
truncation decision to a measured eval delta in the eval slice — don't
truncate up front.
