# Arm A vs Arm B — the harness effect, measured

**Date:** 2026-07-17 · **Arm A harness:** opencode 1.18.3 (`run --auto`), opencode-go models only
**Arm B baseline:** Ornith-1.0-35B via raw completion API, Claude orchestrating (E1.md, E4.md)
**Branches (evince55/minecraft-monitoring):** `arma/e1-minimax-m3` · `arma/e4-deepseek-flash` · `arma/e1-deepseek-pro`

## The question

E1/E4 (Arm B) ended with: *Ornith reliably gets architecture right and reliably
needs one verification-driven fix on a concrete detail — repo-specific in a
codebase, language-specific from scratch. Does giving the model tools (read the
repo, run the validators) close that gap?*

## Results

| Run | Task | Model | Time | Interventions | Grade | The telling detail |
|---|---|---|---|---|---|---|
| B (baseline) | E1 fix | Ornith-35B (local) | ~105s gen | 1 re-prompt + 1 hand-fix | partial | Guessed chart nesting + AM name; rewrote expressions |
| **A-1** | E1 fix | **minimax-m3** | 487s | **0** | **pass** | **Discovered the wrapper-chart double-nesting itself**, preserved expressions verbatim, ran promtool + helm template itself, updated verify.sh unprompted |
| B (baseline) | E4 build | Ornith-35B (local) | ~370s gen | 1 re-prompt (+README by Claude) | partial | Fatal f-string bug; wrong Grafana field |
| **A-2** | E4 build | **deepseek-v4-flash** | 22s + 266s | **1** (question-halt) | **pass** | Stopped to ask an unnecessary clarifying question, then built, **self-discovered Grafana's `database` field**, self-tested with mocks (verified independently) |
| **A-3** | E1 fix | **deepseek-v4-pro** | 965s | 0 | **partial** | Renders and preserves expressions, but justified avoiding `rulerConfig` with a schema constraint that A-1's successful render disproves; relies on chart defaults for rules delivery (statically plausible, path alignment unverified) |

All Arm A runs verified independently (promtool, `helm template` with the repo
values, live behavioral test with mock endpoints for E4) — agent self-reports
were accurate in A-1 and A-2.

## Findings

1. **The harness effect is real and large.** A-1 is the clean confirmation:
   the *exact three errors* that cost Ornith a fact-injection re-prompt (chart
   nesting, Alertmanager service name, expression fidelity) simply didn't occur
   when the model could read the chart and run the validators. Tool access
   converts "strong generator needing a verifier" into "agent that ships."
2. **Model spread is visible on identical task+harness.** minimax-m3 > 
   deepseek-v4-pro on E1: m3 configured everything explicitly and verified it;
   pro leaned on chart defaults and hallucinated a justification (the one
   failure mode tools don't fix: confident wrong claims about what it read).
3. **A new failure mode appears in non-interactive agents: the question-halt.**
   deepseek-v4-flash burned its first session asking "shall I proceed?" on a
   fully-specified task. Cheap to fix in the prompt ("do not ask; decide and
   note decisions in the README") — worth adding to the default task-card
   template.
4. **Harness walls are part of the cost.** First A-1 attempt died in 29s:
   opencode auto-rejects permission requests in non-interactive mode (the agent
   tried to extract a chart into /tmp). `--auto` + an in-repo-scratch
   instruction resolved it. Also: opencode 1.17.9 shipped a broken DB migration
   (`no such column: replacement_seq`) that crashed every run — fixed by 1.18.3.
5. **Cost/latency picture.** Local Ornith: $0 marginal, fastest generation,
   needs an orchestrator. opencode-go models: subscription-included, 4–16 min
   per agentic task, self-sufficient. For the routing table: **agentic
   cloud-budget tier validated for COMPLEX infra fixes** (m3 tier-lead
   placement reconfirmed by direct evidence); flash validated for greenfield
   MODERATE builds with the no-questions prompt clause.

## Caveats

- n=3 runs, one task pair; single grader (Claude), same rubric as Arm B.
- All three E1 solutions share the same runtime gate: ruler firing needs the
  live cluster (ArgoCD sync) — static validation only.
- Arm A "0 interventions" excludes the two harness-level fixes (permission
  mode, client upgrade), which are one-time environment costs, not per-task.

## Next

- Owner picks which E1 solution to merge (A-1's is the most explicit; B's PR #5
  already exists — they're near-equivalent in architecture).
- The true Arm A×Ornith run (opencode pointed at the local 35B endpoint) is now
  the remaining cell of the matrix — it isolates *model* from *harness*
  completely, since Ornith would get the same tools that fixed m3's grounding.
- Fold the question-halt clause and in-repo-scratch clause into the standard
  E-card template.
