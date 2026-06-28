#!/usr/bin/env bash
# Turnkey launcher for the Headroom context-compression proxy — the savings
# engine of the LLMOps compression layer. See AGENTS.md "Context compression"
# and tools/llmops/evals/headroom-eval.md for the eval behind this.
#
# The proxy transparently compresses large tool outputs (build logs, search
# dumps) in the request path. Pair it with the `headroom` MCP server (already
# registered in .opencode/opencode.jsonc) so the model can retrieve originals.
#
# Usage:
#   tools/llmops/headroom-proxy.sh                 # start on :8787 (token mode)
#   HEADROOM_PORT=8799 tools/llmops/headroom-proxy.sh
#
# Then route a client through it (this is what actually reroutes live traffic):
#   Claude Code:  ANTHROPIC_BASE_URL=http://127.0.0.1:${HEADROOM_PORT:-8787} claude
#   opencode / OpenAI-compatible providers (minimax-m3, llama-cpp, deepseek):
#                 point the provider baseURL at http://127.0.0.1:${PORT}/v1
#   Verify:       ~/.headroom-venv/bin/headroom doctor
#
# Install (once):  python3 -m venv ~/.headroom-venv
#                  ~/.headroom-venv/bin/pip install -r tools/llmops/evals/requirements.txt'[mcp,proxy]'
#   IMPORTANT: the package is `headroom-ai`, NOT `headroom` (namespace squatter).
set -euo pipefail

HR="${HEADROOM_BIN:-$HOME/.headroom-venv/bin/headroom}"
PORT="${HEADROOM_PORT:-8787}"
MODE="${HEADROOM_MODE:-token}"   # token = max compression; cache = max prefix-cache hits

if [[ ! -x "$HR" ]]; then
  echo "error: headroom not found at $HR" >&2
  echo "install: python3 -m venv ~/.headroom-venv && \\" >&2
  echo "         ~/.headroom-venv/bin/pip install 'headroom-ai[mcp,proxy]==0.27.0'" >&2
  exit 1
fi

echo "Starting Headroom proxy on http://127.0.0.1:${PORT} (mode=${MODE})"
echo "Route a client through it, then check savings with: $HR doctor"
exec "$HR" proxy --port "$PORT" --mode "$MODE"
