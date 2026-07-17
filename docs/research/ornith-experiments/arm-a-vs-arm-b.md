# Arm A vs Arm B — the harness effect, measured

**Date:** 2026-07-17 (updated: +Ornith+tools cell) · **Arm A harness:** opencode 1.18.3 (`run --auto`), opencode-go models only
**Arm B baseline:** Ornith-1.0-35B via raw completion API, Claude orchestrating (E1.md, E4.md)
**Branches (evince55/minecraft-monitoring):** `arma/e1-minimax-m3` · `arma/e4-deepseek-flash` · `arma/e1-deepseek-pro` · `arma/e1-ornith` · `arma/e4-ornith`

## The question

E1/E4 (Arm B) ended with: *Ornith reliably gets architecture right and reliably
needs one verification-driven fix on a concrete detail — repo-specific in a
codebase, language-specific from scratch. Does giving the model tools (read the
repo, run the validators) close that gap?*

The matrix has two variables: **harness** (raw completion + Claude orchestration
vs opencode agentic loop) and **model** (local Ornith-35B vs cloud opencode-go).
Arm B fixed model, no tools. The cloud Arm A runs fixed harness (tools), varied
model. The final cell — **Ornith + the same opencode tools** — isolates the two:
same model as Arm B, same harness as the cloud runs.

## Results

| Run | Task | Model | Time | Interventions | Grade | The telling detail |
|---|---|---|---|---|---|---|
| B (baseline) | E1 fix | Ornith-35B (local) | ~105s gen | 1 re-prompt + 1 hand-fix | partial | Guessed chart nesting + AM name; rewrote expressions |
| **A-1** | E1 fix | **minimax-m3** | 487s | **0** | **pass** | **Discovered the wrapper-chart double-nesting itself**, preserved expressions verbatim, ran promtool + helm template itself, updated verify.sh unprompted |
| B (baseline) | E4 build | Ornith-35B (local) | ~370s gen | 1 re-prompt (+README by Claude) | partial | Fatal f-string bug; wrong Grafana field |
| **A-2** | E4 build | **deepseek-v4-flash** | 22s + 266s | **1** (question-halt) | **pass** | Stopped to ask an unnecessary clarifying question, then built, **self-discovered Grafana's `database` field**, self-tested with mocks (verified independently) |
| **A-3** | E1 fix | **deepseek-v4-pro** | 965s | 0 | **partial** | Renders and preserves expressions, but justified avoiding `rulerConfig` with a schema constraint that A-1's successful render disproves; relies on chart defaults for rules delivery (statically plausible, path alignment unverified) |
| **A-4** | E1 fix | **Ornith-35B + tools** | 891s | 0 | **partial** | **Got the wrapper-chart nesting RIGHT** (the exact thing it got wrong in Arm B — tools closed it), expressions verbatim. But **never set `alertmanager_url`** → ruler evaluates the alerts but delivers them nowhere, breaking the explicit "Alertmanager routing keeps working" requirement. Self-verification was shallow: ran `helm template` + promtool, declared success, didn't check the render actually routed alerts |
| **A-5** | E4 build | **Ornith-35B + tools** | 417s | 0 | **pass** | **Compiled and ran** — the fatal Arm-B f-string bug did NOT recur once it executed its own code. Self-discovered Grafana's `database` field, self-tested with mocks (verified independently: correct up/down/error, dashboard renders) |

All Arm A runs verified independently (promtool, `helm template` with the repo
values, live behavioral test with mock endpoints for E4) — agent self-reports
were accurate in A-1, A-2, and A-5. A-4's self-report ("renders cleanly") was
true but shallow: the render was clean *and* missing the Alertmanager wiring.

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
5. **Cost/latency picture.** Local Ornith: $0 marginal, ~7–15 min per agentic
   task (local inference, every tool round-trip through the 35B). opencode-go
   models: subscription-included, 4–16 min. For the routing table: **agentic
   cloud-budget tier validated for COMPLEX infra fixes** (m3 tier-lead
   reconfirmed); flash validated for greenfield MODERATE builds with the
   no-questions clause.
6. **The final cell (A-4/A-5) settles model-vs-harness.** Tools closed Ornith's
   Arm-B failure modes on *both* tasks: the E1 wrapper-nesting error and the E4
   f-string compile bug both vanished once Ornith could read the chart / run its
   own code. **Harness effect confirmed for the local model.** But model quality
   still separates Ornith from the cloud tier on the *same* harness:
   - **E4 (greenfield build): Ornith+tools ties the cloud tier** — clean pass,
     self-caught the exact bug that was fatal without tools.
   - **E1 (infra-config fix): Ornith+tools trails by one requirement** — right
     architecture and grounding, but shallow self-verification let the
     `alertmanager_url` requirement slip; minimax-m3 (same harness) caught it.
   The "needs exactly one nudge" pattern is now **task-dependent**: greenfield
   with tools = no nudge; complex-infra with tools = one nudge, and the nudge
   moved from *grounding* (Arm B) to *requirement-completeness / verification
   depth* (Arm A). The failure classes tools DON'T fix — confident wrong claims
   (pro's schema hallucination) and shallow self-checks (Ornith's "renders
   cleanly") — are the residual model-quality signal.

## The completed matrix (routing takeaway)

| | no tools (Arm B) | + opencode tools (Arm A) |
|---|---|---|
| **Ornith-35B (local, $0)** | E1 partial, E4 partial | **E1 partial, E4 pass** |
| **cloud opencode-go** | (not run) | E1 pass (m3) / partial (pro), E4 pass (flash) |

**Practical placement:** the local 35B **+ tools** is a viable $0 agentic tier
for **MODERATE greenfield builds** (fire-and-forget) and for **COMPLEX infra
fixes with a cheap review pass** (the one gap it left, a missing config line,
was caught in a single `grep` of the rendered output). It is not yet
fire-and-forget on COMPLEX. That review is exactly the orchestrator role — so
the standing "strong generator under a verifier" conclusion holds for Ornith
even *with* tools; tools raise the floor (greenfield now clean) without removing
the ceiling (complex still wants a check).

## Caveats

- n=5 runs, one task pair, single grader (Claude), same rubric throughout.
- All E1 solutions share the same runtime gate: ruler firing needs the live
  cluster (ArgoCD sync) — static validation only.
- "0 interventions" for A-4/A-5 counts the graded first completion (comparable
  to the cloud single-shot passes); a re-prompt would very likely fix A-4's
  `alertmanager_url` gap, as the fact-injection re-prompt did in Arm B.
- Harness-level fixes (permission mode, client upgrade, the killed-then-relaunched
  A-4 first attempt) are one-time environment costs, excluded from per-task grades.

## Next

- Owner picks which E1 solution to merge — **A-1 (minimax-m3) is the most
  complete** (only one that wires everything and verified it); PR #7 already
  carries it unified with the earlier Arm B fix.
- Scale from anecdote to statistics: the ~10-task Ornith-vs-cloud benchmark on
  real flywheel-cluster tasks (each graded run doubles as outcome-labelled data).
- Fold the no-questions and in-repo-scratch clauses into the standard E-card.
- Fix rufigen01's wifi-adapter power-save — the dominant reliability tax on
  every agentic Ornith run (many round-trips, any mid-run tailnet drop kills it).
