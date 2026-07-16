# Capability probe: testing the Routing-SOL success-transfer assumption

**Date:** 2026-07-14 · **Branch:** `feat/capability-probe` (stacked on `feat/routing-sol-eval`) · **Probe model:** Qwen3.5-9B-MLX-4bit on oMLX (M4 Air)

## 1. Question

The Routing-SOL hybrid bound says $747.11 (66.0%) of labeled imputed spend is
addressable — **assuming** every confidently-classified frontier success would
still have succeeded on its tier's cheap chain-lead. This probe tests that
assumption the only way available at solo-dev scale: replay each over-routed
session's originating task **single-shot** against a local-tier model and
grade the responses.

## 2. Method

`evals/capability_probe.py` selects the top over-routed sessions (hybrid
confidence — the same selection the SOL bound claims), prompts the local model
for a concrete solution (files + approach + key code), and records responses
for **separate, non-automated grading** (rubric below). Probe model is the
on-device 9B because the homelab 35B chain-lead was unreachable — a **weaker
proxy, so passes are conservative evidence; failures are inconclusive for the
35B.**

Rubric: **pass** = correct approach, plausible files/APIs, key change
essentially right · **partial** = right direction, wrong/missing specifics ·
**fail** = wrong frame or vacuous. Weights 1.0 / 0.5 / 0.0. Grader:
claude-fable-5, single grader (disclosed limitation). Grades:
`evals/probe_results/2026-07-14-grades.json` (gitignored with the responses —
they contain session text).

## 3. Results (8 sessions, $741.77 at stake, mean 32.7s/response)

| Tier | Savings | Grade | One-line rationale |
|---|---|---|---|
| COMPLEX | $571.24 | **partial** | Right improvement genre (async/queue/progress), hallucinated repo structure |
| SIMPLE | $66.26 | fail* | *Instrumentation artifact: probe prompt mis-framed a sysadmin question as iOS coding* |
| CRITICAL | $33.47 | fail | Invented feature work; no contact with real deployment needs |
| CRITICAL | $26.67 | **partial** | Directionally matches the real hardening track |
| COMPLEX | $16.65 | fail | Audit task requires repo access; single-shot cannot do it (model said so, then confabulated) |
| SIMPLE | $12.23 | **pass** | Named the real `PlayerManager.swift`, correct pre-resolve design |
| SIMPLE | $11.79 | **partial** | Workable research-script approach |
| SIMPLE | $3.45 | fail* | *Same framing artifact (how-to question answered as feature build)* |

**Expected savings (grade-weighted):**

- Local-tier pool (SIMPLE+COMPLEX, chain-lead = free local 35B): **$303.75 of
  $681.63 → 44.6%** of the pool, ≈ **27% of total labeled spend** — roughly
  **half the 66% SOL ceiling survives the probe**.
- CRITICAL rows ($60.14) are excluded from the local claim: their chain-lead
  is minimax-m3 (paid cloud), which a 9B probe cannot test.

## 4. The finding that matters most: concentration risk

One session carries **77% of the pool** ($571.24). The estimate is hostage to
its grade: pass → $583 (86%), fail → $18 (3%), partial (assigned) → $304
(45%). A single-shot grade on one mega-session is the weakest link in the
chain — **the next probe iteration should multi-shot that session type** (and
its cluster, once S3 clustering exists) rather than widening n indiscriminately.

Secondary finding: 2 of 4 fails are **instrumentation artifacts** — the probe
prompt hard-frames every task as iOS coding, which derailed two non-coding
tasks (a sysadmin question, a how-to). Probe v2 should frame the prompt from
the task itself. Removing artifact rows entirely: $303.75 of $611.92 → 49.6%.

## 5. Limitations

- Single-shot ≠ agentic session: real routed sessions have repo access and
  tools; two fails (audit, improvements-sweep) reflect that gap more than
  model capability.
- 9B proxy for the 35B chain-lead: conservative for passes, inconclusive for
  fails.
- n=8, single grader, one probe model, imputed economics throughout.

## 6. Next

1. ~~Probe v2: task-adaptive prompt framing; multi-shot the $571 session type.~~ **Done — see §7.**
2. Rerun against the homelab 35B when reachable (the actual chain-lead).
3. Wire probe grades back into the SOL report as a `probe_adjusted_headroom`
   so dashboard consumers see ceiling AND estimate side by side.

## 7. Probe v2 (same day): task-adaptive framing + multi-shot top session

Changes: the prompt no longer asserts an iOS-coding premise (response form
follows the task's own nature), and the highest-savings session gets 3 samples
(server-default temperature; per-sample grades averaged). Same 9B proxy, so
v1→v2 deltas are attributable to instrumentation.

| Metric | v1 | **v2** |
|---|---|---|
| Local-tier pool | $681.63 | $717.34 |
| Expected savings | $303.75 (44.6%) | **$172.88 (24.1%)** |
| Artifact fails | 2 | **0** (both recovered to partial) |
| Mega-session weight | 0.5 (1 sample) | **0.167** (fail/partial/fail) |

**The estimate went DOWN, and that is the finding.** The framing fix recovered
both artifact rows (+$50 weighted), but multi-shotting the mega-session cut
its weight from 0.5 to 0.167: with the false premise removed, all three
samples honestly disclose they lack the repo, and two of three then mis-assume
an on-device yt-dlp architecture. v1's higher number leaned on one
confident-sounding sample. Sensitivity to the mega-session's weight remains
the dominant uncertainty: 0 → 10.7%, assigned 1/6 → 24.1%, 0.5 → 37.5%,
1.0 → 64.4%.

Reproducible signal: the one concrete engineering session passes in **both**
runs (real `PlayerManager.swift` path, correct pre-resolve design). The
practical routing implication: small, concrete tasks are dependable local
wins; mega/agentic sessions (80% of the pool by dollars) are where single-shot
local capability is weakest — keep them on the frontier until an agentic probe
says otherwise.

New instrumentation finding: the ledger's task-text cap truncated two probe
prompts mid-sentence (the 9B correctly flagged one as incomplete and asked for
the rest — proper behavior, graded 0.5, capability untested). **Probe v3
should source full first-prompts from session transcripts, not the capped
ledger field.**

Revised bottom line: **expected local-tier savings ≈ $173 (24% of the pool,
band ~11–38% on mega-session sensitivity), vs the 66% SOL ceiling.** Grades:
`evals/probe_results/2026-07-14-grades-v2.json`.

## 8. 35B rerun (same day): the actual chain-lead, measured

Ornith-1.0-35B (Q4_K_M, llama.cpp @ `<tailscale-ip>`:8080) came online — the
real local-tier class, ending the 9B-proxy era. Identical selection (oMLX 9B
hybrid) and prompts (v2); only the probe model changed. Mean latency 21.5s —
*faster* than the on-Air 9B.

| Metric | 9B proxy (v2) | **Ornith-35B** |
|---|---|---|
| Expected savings | $172.88 (24.1%) | **$409.15 (57.0%)** |
| Mega-session weight | 0.167 | **0.5** (partial ×3) |
| Passes | 1 | **2** |
| Mega sensitivity band | 11–38% | 17–97% (0.5 assigned → 57%) |

**The proxy-conservatism claim is now measured, not assumed: the real
chain-lead recovers most of the SOL ceiling (57% vs 66%).** Highlights: the
NVMe row is a clean pass (exact right `diskutil`/`system_profiler` commands
and it *predicted the unformatted-disk finding*); the audit row inferred the
real high-risk subsystems unprompted; all mega-session samples converge on
the app's actual design patterns.

Two grading notes with methodological weight:
1. **Agentic-format artifact**: on the concrete engineering task the 35B
   responded *as an agent* — emitting the exact right shell commands to read
   `PlayerManager.swift` and the tracker — but under single-shot rules that's
   a plan, not a fix (partial; the 9B "passed" it with a direct design).
   Single-shot probing systematically understates agentic-native models →
   **the v3 agentic probe is now the highest-leverage instrumentation fix.**
2. **Fabricated provenance**: the research row claims it "cross-referenced
   star-history.com" — impossible without web access; graded down for
   integrity. Local-tier routing of research tasks needs tool access AND
   provenance checks.

**R1 final arc: 1.1% (keyword) → 66% (SOL ceiling) → 24% (9B proxy) →
57% (real 35B chain-lead).** Routing policy stands: concrete tasks and
questions are dependable local wins; mega/agentic sessions await the agentic
probe. Grades: `evals/probe_results/2026-07-14-grades-35b.json`.
