# Headroom context-compression — LLMOps eval & integration decision

**Date:** 2026-06-28 · **Package:** `headroom-ai==0.27.0` · **Harness:** [`headroom_fidelity_eval.py`](./headroom_fidelity_eval.py) · **Raw data:** [`headroom-results.json`](./headroom-results.json)

## TL;DR

`headroom-ai` is **safe to add to the Aria LLMOps workflow** as a tool-output
compressor. In realistic build-fix sessions it cuts tokens **~33–65%
(avg 38%), deterministically, with zero loss of the actionable error signal**.
The headline "96%+" only applies to highly-repetitive, blank-line-delimited
logs and is format-fragile — treat it as a ceiling, not the expected number.
**Source code does not compress (0%).** Adopt it for logs/tool output, not as a
general context shrinker.

## Why we evaluated this

Aria is an LLMOps practice vehicle; cost-per-task is a first-class deliverable.
The agent loop is `xcodebuild`-heavy (build → read log → fix → rebuild), and
build logs are the dominant token sink. `headroom-ai` compresses tool outputs
before they reach the model. A compressor that drops the wrong line is worse
than none, so we gated adoption on two questions: **savings** and **fidelity**.

## Method

`headroom_fidelity_eval.py` (offline, `compress()` library API, tiktoken counts):

- **Part A — content-type ceiling:** forced-aggressive config
  (`compress_user_messages=True, protect_recent=0`) on single payloads, to
  isolate how compressible each content type is.
- **Part B — realistic fidelity:** a 3-iteration build-fix conversation under
  the **default** config. We inject known "needles" (a compile error, a test
  failure, a linker error) into either the most-recent log (which headroom
  protects) or an older, compressible log, then check the needle's key
  substrings survive. Each case runs 3× to check determinism.

## Results

### Part A — what actually compresses (format-sensitive ceiling)

| payload | before | after | saved |
|---|--:|--:|--:|
| `PlayerManager.swift` (real source) | 10,296 | 10,296 | **0%** |
| `backend/app.py` (real source) | 5,715 | 5,715 | **0%** |
| xcodebuild log, single-newline | 7,000 | 7,000 | **0%** |
| xcodebuild log, **blank-line-delimited** | 7,034 | 135 | **98%** |

**Finding:** compression is deterministic but **format-sensitive**. Blank-line
(paragraph) delimited repeated records trigger SmartCrusher's array-style
crushing (~98%); the *same* log with single-newline joins gets 0%. Real
`xcodebuild` output is blank-line-delimited, so the high numbers are reachable
in practice — but do not depend on them. **Dense source code has no slack: 0%.**

### Part B — realistic build-fix session (default config, the integration path)

| needle | position | before | after | saved | signal kept? | deterministic? |
|---|---|--:|--:|--:|:--:|:--:|
| compile_error | recent | 25,029 | 16,890 | 33% | ✅ KEPT | ✅ |
| compile_error | old | 25,029 | 16,890 | 33% | ✅ KEPT | ✅ |
| test_failure | recent | 25,046 | 8,833 | 65% | ✅ KEPT | ✅ |
| test_failure | old | 25,045 | 16,906 | 32% | ✅ KEPT | ✅ |
| linker_error | recent | 25,025 | 16,886 | 33% | ✅ KEPT | ✅ |
| linker_error | old | 25,025 | 16,886 | 32% | ✅ KEPT | ✅ |

**Findings:**
- **Fidelity: 6/6 signals preserved, even when the needle is in a compressed
  older log** — compression is content-aware (crushes boilerplate compile lines,
  keeps error/warning/failure lines), not "drop old messages." No message was
  dropped; one redundant intermediate log was crushed 98% in place.
- **Deterministic** across 3 runs each (identical token counts and recall).
- **Recent context is protected** — the latest log (the one the agent acts on)
  is never compressed. This is *why* it's safe for a build-fix loop: you act on
  the newest log; compression only reclaims superseded ones.
- **Realistic savings: ~33–65%** (avg 38%), lower than Part A's ceiling because
  successive real logs differ and recent context is protected.

## Decision

**Adopt `headroom-ai` as the tool-output compression layer**, scoped to
logs / build output / tool dumps — not source code. Expected steady-state
saving in this workflow is ~30–40%, with occasional large wins on repetitive
logs. Fidelity and determinism are good enough for autonomous loops.

### Recommended integration (opt-in, not yet wired into live config)

Run headroom as an **MCP server** so opencode / Claude Code sessions get
compression transparently:

```bash
python3 -m venv ~/.headroom-venv
~/.headroom-venv/bin/pip install -r tools/llmops/evals/requirements.txt
# expose headroom_compress / headroom_retrieve / headroom_stats as an MCP server
~/.headroom-venv/bin/headroom mcp        # or `headroom proxy --port 8787`
```

Then register that MCP server in the agent config (`.opencode/`). Left as an
explicit opt-in because it changes the live multi-agent setup and runs a
persistent local process.

## Caveats / risks

- **Supply-chain — pin the name.** The package is **`headroom-ai`**. Bare
  `pip install headroom` installs an **unrelated, shell-executing** CLI agent
  (`github.com/SUNKENDREAMS/headroom`) that collides on the same `headroom`
  import namespace. Always install via the pinned `requirements.txt`.
- **Format fragility.** Savings depend on log structure (blank-line delimiting).
  Don't promise a fixed %; measure per workload.
- **Beta software**, ~2 weeks of viral attention. Re-run this eval on version
  bumps before trusting new releases.
- **Not verified here:** the MCP-server / proxy path end-to-end inside a live
  opencode session (this eval used the offline library API). Verify before
  enabling autonomously.

## Reproduce

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r tools/llmops/evals/requirements.txt
python3 tools/llmops/evals/headroom_fidelity_eval.py --json tools/llmops/evals/headroom-results.json
```
