# llama-swap deploy configs

## `nomic-embed.yaml` — embedding model for Aria library search

Registers the `nomic-embed` alias (nomic-embed-text-v1.5, 768-dim) that the
Aria backend's `/api/library/query` hybrid retrieval calls via OpenAI-format
`POST /v1/embeddings` (Aria repo, RAG Slice 2 PR).

### Owner runbook (on the Windows box that runs llama-swap)

llama-swap runs on the **Windows GPU box**, not the Linux host that runs the
Aria backend. The Aria backend reaches it over the tailnet, which adds two
Windows-specific requirements (step 3).

1. Download the GGUF into the models dir llama-swap already uses (whatever
   directory holds the chat-model GGUFs):

   ```powershell
   cd $env:LLAMA_MODELS_DIR   # or the models directory in the llama-swap config
   curl.exe -LO https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q8_0.gguf
   ```

   (`nomic-embed-text-v1.5.f16.gguf` is the higher-fidelity alternative.)

2. Merge the `models."nomic-embed"` entry (plus macros, if your config does
   not already define them) into the config file the running llama-swap was
   started with — llama-swap loads a single config file. Adapt the macro
   paths to the Windows layout (`llama-server.exe`, Windows model paths).
   For a standalone smoke test, `nomic-embed.yaml` is valid on its own.

3. Make it reachable from the tailnet: llama-swap must listen beyond
   localhost (`0.0.0.0:8080` or the Tailscale interface), and Windows
   Firewall must allow inbound TCP :8080 (scoping the rule to the Tailscale
   subnet `100.64.0.0/10` is enough).

4. Restart llama-swap, then smoke-test locally and from another tailnet
   machine (**:8080**, the same endpoint `LLMOPS_SWAP_ENDPOINT` defaults to):

   ```bash
   curl -s http://<windows-tailscale-ip>:8080/v1/embeddings \
     -H 'Content-Type: application/json' \
     -d '{"model":"nomic-embed","input":["search_query: mellow guitar"]}' \
     | jq '.data[0].embedding | length'   # expect 768
   ```

5. Point the Aria backend at it:
   `ARIA_EMBED_URL=http://<windows-tailscale-ip>:8080/v1/embeddings` on the
   Linux backend host (the Aria repo's `aria-backend.service` commits a
   placeholder IP for this — swap in the real one at deploy time). The box
   sleeps; while it's down, Aria library queries degrade to BM25-only by
   design.

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
