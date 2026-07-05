# Single-endpoint local inference via llama-swap

Runs the **35B MoE executor** and the **9B classifier** behind **one port** on the
Radeon RX 7600 XT (16 GB VRAM, RDNA3 / gfx1102). The router
([`llmops.py`](../../llmops.py)) already sends the model name in every request, so
one llama-swap port routing by model name is all that's needed — no router logic
changes, just a config flip (`LLMOPS_INFERENCE_MODE=swap`).

> **llama-swap is a separate Go proxy** ([github.com/mostlygeek/llama-swap](https://github.com/mostlygeek/llama-swap)),
> **not** a `llama.cpp` subcommand. Install its release binary; it launches
> `llama-server` processes for you per this config.

## Files

| File | What |
|------|------|
| `config.yaml` | llama-swap config: both models + a pinned-resident routing group |
| `smoke-test.sh` | 2-model curl test — hits the one port with each model name |
| `README.md` | this runbook |

---

## ⚠️ Step 0 — VERIFY VRAM FIT BEFORE COMMITTING (do this on the homelab)

16 GB is **tight**. The combined footprint is an **estimate (~13–14 GB)** — it has
**not** been measured on your hardware and I can't measure it from a dev machine.
Boot each model **alone** under plain `llama-server` and read the real numbers.

```bash
# 35B alone — note the buffer + KV lines, then Ctrl-C
HSA_OVERRIDE_GFX_VERSION=11.0.0 \
  "$LLAMA_SERVER_BIN" --model "$LLAMA_MODELS_DIR/qwen3.6-35b-a3b-q8_k.gguf" \
  --port 9099 --cpu-moe -ngl 99 -c 32768 -fa -ctk q8_0 -ctv q8_0 --no-webui 2>&1 \
  | grep -E "buffer size|KV self|flash|CUDA|ROCm|offloaded"

# 9B alone — same idea
HSA_OVERRIDE_GFX_VERSION=11.0.0 \
  "$LLAMA_SERVER_BIN" --model "$LLAMA_MODELS_DIR/9b_mythos_q4_k_m.gguf" \
  --port 9098 -ngl 99 -c 8192 -fa -ctk q8_0 -ctv q8_0 --no-webui 2>&1 \
  | grep -E "buffer size|KV self|flash|offloaded"
```

Add up, per model: `llm_load_tensors: ... buffer size` (VRAM weights) **+**
`llama_kv_cache_init: ... KV self size` **+** ~0.3 GB compute buffers. Then:

```
35B (VRAM weights + KV)  +  9B (VRAM weights + KV)  +  ~1 GB desktop/display  <  16 GB ?
```

Rough target: 9B ≈ ~6 GB, 35B ≈ ~5–8 GB. If the sum blows past ~15 GB, apply a
fallback below **before** running both together.

### Confirm Flash Attention actually engaged

q8_0 KV (`-ctk/-ctv q8_0`) generally **requires** `-fa`. In the boot-alone output,
confirm a line indicating flash attention is **on/enabled**. On gfx1102 + ROCm it
often won't load without `HSA_OVERRIDE_GFX_VERSION=11.0.0` (already injected via
each model's `env:` in `config.yaml`). If `-fa` still won't engage:

- Try the **Vulkan** llama.cpp backend (frequently smoother on RDNA3), then delete
  the `HSA_OVERRIDE_GFX_VERSION` `env:` blocks from `config.yaml`.
- If `-fa` is truly unavailable, **q8 KV is off the table** → f16 KV roughly
  **doubles** the KV budget. Compensate by lowering the 35B context: drop
  `-c 32768` to `-c 16384` and remove `-ctk/-ctv q8_0` on both models.

### `-fa` flag form
The config uses bare `-fa` (boolean). Newer llama.cpp accepts `-fa on|off|auto`;
if your build rejects bare `-fa`, change it to `-fa on`. Older builds without
`--cpu-moe` need `--override-tensor '\.ffn_(up|down|gate)_exps\.=CPU'` instead
(commented in `config.yaml`).

---

## Step 1 — Model prerequisites

- **35B**: `qwen3.6-35b-a3b-q8_k.gguf` (already your executor model).
- **9B q4_K_M**: the config expects `9b_mythos_q4_k_m.gguf` (~5 GB). Your currently
  deployed classifier is **q8** (`9b_mythos_q8.gguf`, ~9–10 GB) — too heavy to sit
  resident next to the 35B. **Obtain or quantize a q4_K_M copy** and place it in
  `$LLAMA_MODELS_DIR`, or edit the filename in `config.yaml` to whatever q4 you have.
  (Quantize from an existing gguf: `llama-quantize in.gguf 9b_mythos_q4_k_m.gguf Q4_K_M`.)

---

## Step 2 — Set paths and run llama-swap

`config.yaml` reads two env vars at load time (unset ⇒ fail-fast):

```bash
export LLAMA_SERVER_BIN=/home/eugene/llama.cpp/build/bin/llama-server   # your path
export LLAMA_MODELS_DIR=/home/eugene/models                              # your path

llama-swap --config deploy/llama-swap/config.yaml --listen 0.0.0.0:8080
```

(Or replace the `${env...}` macro values in `config.yaml` with literal paths.)
Watch the log until **both** models report ready (they preload at startup).

## Step 3 — Smoke test the one port

```bash
deploy/llama-swap/smoke-test.sh 127.0.0.1:8080     # needs: curl, jq
```

It lists `/v1/models` (both must appear), then POSTs a chat completion to
`qwen35b` (with `enable_thinking:false`, or Qwen returns empty content) and to
`9b-classifier`, asserting HTTP 200 + non-empty content and printing latency.
Green all the way ⇒ safe to wire the router. (Script uses Linux `date +%s%3N`.)

## Step 4 — Flip the router to swap mode

`llmops.py` supports two topologies via one env var — no code edit to switch:

```bash
export LLMOPS_INFERENCE_MODE=swap            # default is "dual" (two ports)
# optional: point at a non-default endpoint (both clients use it)
export LLMOPS_SWAP_ENDPOINT=http://127.0.0.1:8080/v1
```

In `swap` mode both clients target the one endpoint and the model names become the
llama-swap keys (`qwen35b` / `9b-classifier`). Verify:

```bash
LLMOPS_INFERENCE_MODE=swap python3 -c \
  'import llmops as m; print(m.LOCAL_BASE_URL, m.LOCAL_MODEL_NAME, "|", m.CLASSIFIER_BASE_URL, m.CLASSIFIER_MODEL)'
# -> http://192.168.1.84:8080/v1 qwen35b | http://192.168.1.84:8080/v1 9b-classifier
```

Any explicit `LLMOPS_LOCAL_*` / `LLMOPS_CLASSIFIER_*` var still overrides the
mode default. Unset `LLMOPS_INFERENCE_MODE` (or set `dual`) to fall straight back
to the two-port layout.

### systemd (optional)
Point a `llama-swap.service` unit at the command in Step 2 with the two
`Environment=` paths set, and set `LLMOPS_INFERENCE_MODE=swap` wherever the router
runs. Retire the two separate `llama-server` units once the smoke test is green.

---

## Schema compatibility note

`config.yaml` uses the **current** llama-swap schema, where groups live under
`routing.router.settings.groups`. **Older releases** used a **top-level** `groups:`
key. Check with `llama-swap --version`; if yours predates the routing engine,
replace the `routing:` block with:

```yaml
groups:
  resident:
    swap: false
    exclusive: false
    persistent: true
    members: ["qwen35b", "9b-classifier"]
```

Everything above `routing:` (models, macros, hooks) is unchanged.
