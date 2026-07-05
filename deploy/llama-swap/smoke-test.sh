#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 2-model smoke test for the single llama-swap endpoint.
#
# Confirms BOTH models answer on the SAME port, routed by model name, BEFORE
# wiring the router. Run this after `llama-swap --config config.yaml` is up and
# both models have finished loading (watch the llama-swap log for readiness).
#
# Usage:
#   ./smoke-test.sh                 # defaults to 127.0.0.1:8080
#   ./smoke-test.sh 100.76.103.1:8080
#   ENDPOINT=host:port ./smoke-test.sh
# ---------------------------------------------------------------------------
set -euo pipefail

ENDPOINT="${1:-${ENDPOINT:-127.0.0.1:8080}}"
BASE="http://${ENDPOINT}"
EXEC_MODEL="${LLMOPS_LOCAL_MODEL:-qwen35b}"
CLASSIFIER_MODEL="${LLMOPS_CLASSIFIER_MODEL:-9b-classifier}"

pass=0; fail=0
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

require() { command -v "$1" >/dev/null 2>&1 || { red "missing dependency: $1"; exit 2; }; }
require curl
require jq

echo "== endpoint: ${BASE} =="

# 0) Both models should be visible on the ONE port -------------------------
echo
echo "-- /v1/models (both should be listed on this single port) --"
models_json="$(curl -fsS "${BASE}/v1/models")" || { red "FAIL: /v1/models unreachable at ${BASE}"; exit 1; }
echo "${models_json}" | jq -r '.data[].id' | sed 's/^/   /'
for m in "${EXEC_MODEL}" "${CLASSIFIER_MODEL}"; do
  if echo "${models_json}" | jq -e --arg m "$m" '.data[] | select(.id==$m)' >/dev/null; then
    green "   ok: ${m} listed"; pass=$((pass+1))
  else
    red   "   MISSING: ${m} not in /v1/models"; fail=$((fail+1))
  fi
done

# Helper: POST a chat completion, assert 200 + non-empty content, print latency.
# $1 model name, $2 user prompt, $3 extra JSON (merged into body, e.g. thinking off)
probe() {
  local model="$1" prompt="$2" extra="${3:-{}}"
  echo
  echo "-- chat/completions  model=${model} --"
  local body start end ms content
  body="$(jq -cn --arg m "$model" --arg p "$prompt" --argjson x "$extra" \
    '{model:$m, messages:[{role:"user",content:$p}], max_tokens:64, temperature:0} * $x')"
  start="$(date +%s%3N)"
  local resp http
  resp="$(curl -sS -w '\n%{http_code}' -X POST "${BASE}/v1/chat/completions" \
            -H 'Content-Type: application/json' -d "${body}")" || { red "   FAIL: request errored"; fail=$((fail+1)); return; }
  end="$(date +%s%3N)"; ms=$((end-start))
  http="$(printf '%s' "${resp}" | tail -n1)"
  resp="$(printf '%s' "${resp}" | sed '$d')"
  if [ "${http}" != "200" ]; then
    red "   FAIL: HTTP ${http}"; echo "${resp}" | head -c 400 | sed 's/^/   /'; fail=$((fail+1)); return
  fi
  content="$(echo "${resp}" | jq -r '.choices[0].message.content // empty')"
  if [ -z "${content}" ]; then
    red "   FAIL: HTTP 200 but EMPTY content (for qwen, check enable_thinking=false)"; fail=$((fail+1)); return
  fi
  green "   ok: HTTP 200, ${ms}ms, content: $(echo "${content}" | head -c 80 | tr '\n' ' ')"
  pass=$((pass+1))
}

# 1) 35B executor. Qwen 3.6 returns EMPTY content unless thinking is disabled,
#    so pass the same chat_template_kwargs the router uses.
probe "${EXEC_MODEL}" "Reply with exactly the word: pong" \
      '{"chat_template_kwargs":{"enable_thinking":false}}'

# 2) 9B classifier — a narrow one-word tier call, its real job.
probe "${CLASSIFIER_MODEL}" "Classify this task in ONE word (SIMPLE/MODERATE/COMPLEX): fix a typo in the README."

echo
echo "==================================================="
if [ "${fail}" -eq 0 ]; then
  green "ALL CHECKS PASSED (${pass}). Both models respond on ${BASE} — safe to wire the router (LLMOPS_INFERENCE_MODE=swap)."
  exit 0
else
  red "${fail} CHECK(S) FAILED, ${pass} passed. Do NOT wire the router yet — see llama-swap logs."
  exit 1
fi
