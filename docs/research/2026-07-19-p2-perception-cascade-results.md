# P2 — the perception cascade: machinery proven, hypothesis not reproduced (on web)

**Date:** 2026-07-19 · **Design:** `experiments/perception_cascade/README.md` · **Follows:** `2026-07-19-p1-ambiguity-probe.md`
**Headline:** minimax-m3 fixed **12/12** planted rendering bugs with honest claims across all
three arms — including a deception bug built to defeat code reading. The failure mode that
motivated the cascade (misread the render, loop on a bad fix, claim success) **did not occur
in this regime**. Both cascade tiers are validated end-to-end at **zero marginal dollars**,
and the experiment's collateral findings are worth more than its main effect.

## What ran

3 arms × 4 bugs, one run each, on copies of the real career-portal site. The builder was
always `opencode-go/minimax-m3` with an identical prompt; arms differed only in the
verification tools present:

| Arm | Tooling in ./tools |
|---|---|
| A | `screenshot.sh` (page + console capture) — the "original failure mode" baseline |
| B | + `verify.sh` — tier 0: local Qwen2.5-VL-3B answers strict YES/NO about a fresh screenshot (free, on the Air via mlx-vlm) |
| C | + `diagnose.sh` — tier 1: headless `claude -p` oracle on the Max plan (read-only `Read/Glob/Grep`, 3-call budget, verdict relayed verbatim) |

Planted bugs, each a one-token realistic edit committed with a plausible message:
**b1** tagline color `var(--text-1)`→`var(--bg-1)` (invisible text) · **b2** proof-chip
margin sign flip (overlaps CTAs) · **b3** renamed JS export (module dies; console-only
evidence; page keeps seed values) · **b4** accent token `#2bd7d6`→`#2b2d36` (the page's
entire cyan system goes dark; comment still says "electric cyan"; symptom names no element).

Grading was **deterministic Playwright assertions on the rendered page** (computed contrast,
bounding-box intersection, console content, luminance) — validated in both directions before
any run: every planted state fails, the pristine site passes. `false_success` = builder said
DONE while the grader failed.

## Results

| Cell | Fixed | Claimed | False success | verify calls | escalations | Wall |
|---|---|---|---|---|---|---|
| b1 × A/B/C | ✓ ✓ ✓ | DONE ×3 | 0 | 0 / 1 / 1 | 0 | 169s / 122s / 221s |
| b2 × A/B/C | ✓ ✓ ✓ | DONE ×3 | 0 | 0 / 1 / 2 | 0 | 193s / 84s / 203s |
| b3 × A/B/C | ✓ ✓ ✓ | DONE ×3 | 0 | 0 / 1 / 2 | 0 | 114s / 75s / 239s |
| b4 × A/B/C | ✓ ✓ ✓ | DONE / *timeout* / DONE | 0 | 0 / 0 / 1 | 0 | 273s / 720s† / 249s |

**12/12 fixed, 12/12 exact minimal correct diffs** (identical one-liners across arms),
**0 false successes, 0 escalations**. †b4-B's fix was complete and correct; see finding 3.

## Reading the null honestly

The arms did not separate because **perception was never load-bearing on this class of bug**.
Two mechanisms did the work my design meant vision to do:

1. **The symptoms and code were transparent.** b1–b3's symptoms named their element, and
   grep or the console message (b3's console literally prints the missing export name) led
   straight to the cause. Single-cause + clean repro loop (edit → reload) is minimax's home
   turf regardless of what it can see.
2. **The deception bug was defeated by documentation.** b4's value looked plausible, but the
   adjacent comment — *electric cyan* — betrayed it. Arm A's claim reasons about exactly that
   mismatch. A well-commented token system lets a cheap model diagnose an "invisible"
   rendering bug entirely from text.

What the null does **not** cover is the regime the owner's original observation came from:
SwiftUI/iOS (no DOM, no console equivalent, minutes-long build loop), multi-cause bugs,
sparsely documented code, and vague-locus symptoms. Every solved bug here had a
seconds-long edit→reload loop and a text trail. The cascade remains the design for the
regime where those are absent — now with every component proven.

## Collateral findings (the real value)

**1. Tier-0 spatial verdicts flip on phrasing.** In b2-C the local VLM answered
"are the chips below the buttons with no overlap?" → YES (correct), then 14 seconds later
"do the chips overlap the buttons?" → **also YES** — a self-contradiction on the same fixed
page. The builder noticed and adjudicated correctly. Boundary for the free tier: presence
and visibility questions are reliable; **negations and spatial relations are not**. Tier-0
gate questions should be phrased as positive presence checks, ideally asked twice with
inverted polarity and required to disagree.

**2. Minimax never self-escalates.** Across four C-cells the oracle was available and never
called — consistent with P1's finding that small models under-reach for expensive help. If
escalation matters, the *harness* must trigger it (e.g. after N failed verify cycles), not
the builder's judgment.

**3. A stalled verifier deadlocked a finished run.** b4-B diagnosed the accent bug, applied
the correct fix, then obediently ran the *required* verify step — and mlx-vlm stalled on
load, eating the remaining 470s of budget. The protocol turned an infrastructure hiccup into
a timeout on a *correct* run. Fixed in the harness: verify.sh now hard-caps at 120s and
degrades to `UNCLEAR` rather than blocking. General LLMOps rule: **any mandatory
verification step needs its own timeout and a degraded verdict, or the gate becomes the
outage.**

**4. Honest claims throughout.** Zero false successes in 12 runs, including arm A with no
verification requirement. The claim-inflation the owner observed on iOS did not reproduce
here — evidence that it is regime-dependent (fast feedback loops leave little room to
believe your own wrong fix), not a fixed property of the model.

**5. The Max-plan oracle works exactly as designed** (verified in preflight + harness):
headless `claude -p` reads screenshots, runs read-only, returns structured diagnoses, costs
nothing beyond plan capacity. Permission note: headless mode denies all tools by default —
`--allowedTools "Read" "Glob" "Grep"` is both necessary and the right least-privilege set
for an oracle that must never contaminate the builder's workspace.

## Cost

Zero incremental dollars. 13 builder runs on the opencode-go subscription (~45 min wall
total), tier-0 on local compute, tier-1 preflight + harness probes on Max-plan capacity
(the in-run oracle was never invoked). One 2.2GB model download (Qwen2.5-VL-3B-4bit,
internal disk — the NVMe lost TCC authorization mid-session, pending owner re-grant).

## What would falsify the null (next increments, owner-gated)

1. **The iOS round** — the original regime: plant SwiftUI rendering bugs in Aria, builder
   drives the simulator via xcodebuildmcp. Slow loop + no console/DOM + vision-dependent.
   This is where arms should separate if the cascade thesis is right. Heavier harness
   (sim control for opencode), so gated.
2. **Comment-stripped / misdocumented variant** — same web bugs with comments removed or
   *wrong* (the crueler test: documentation that lies). Cheap to run with this harness.
3. **Multi-cause bugs** — two interacting edits where fixing either alone changes nothing;
   the regime where diagnosis loops actually spiral.

Until one of those separates the arms, the operational conclusion stands: **for
well-documented static-web work, a cheap builder + a capture script is sufficient — spend
nothing more.** The cascade's budget should be reserved for the regimes above.

---

# iOS round (2026-07-19) — INVALID: harness contamination, not a cascade test

The iOS round was meant to be the falsification regime the web null pointed at:
slow build loop, no console/DOM, perception genuinely load-bearing. It did **not**
test the cascade. It is reported here in full because the contamination is itself
the finding, and because it damaged the real repo (found and reverted).

## What was built and validated

Two planted SwiftUI bugs in real Aria code, both confirmed to reproduce on-device
via bidirectional pixel grading (`grade_ios.py`, nearest-baseline classification on
cropped ROIs, sim clock pinned): **i1** `DesignTokens.textSecondary` dark `0.62→0.12`
(a copy of the adjacent `cardSurface` value; ghosts all secondary text app-wide),
and **i2** a `ContentView` ZStack reorder that paints the theme background over the
whole UI (launches to a flat dark screen). The runner's own build→launch→screenshot
→grade is sound; the grader's verdicts are correct.

## What actually happened

The builder (opencode/minimax-m3) **ignored the injected `./tools/run_app.sh`** and
used its own `xcodebuildmcp` MCP server, which **resolves the build by scheme name**.
Launched in a sandboxed repo copy, minimax:

1. failed to find `run_app.sh` in its cwd, then searched `/Users/chait/MusicAppIOS`
   with absolute paths — escaping the sandbox;
2. built and screenshotted via xcodebuildmcp, which opened the **real** `Aria.xcodeproj`
   (pristine), not the planted copy;
3. **edited files in the real repo** — `ThemeManager.swift` (0.62→0.72) and
   `MiniPlayerView.swift` (`.secondary`→`tokens.textSecondary`).

So every builder saw a **healthy app** and reported truthfully on it: i1-B/i1-C claimed
DONE ("bug not reproducible", RGB sampled at the *healthy* ~152, not the planted ~31);
all three i2 cells reported STUCK ("cannot reproduce, the UI works fine"). The planted
bug was in the sandbox repo they never built. **The cascade tools were never invoked
(0 verify, 0 escalate across all six cells)** because minimax used its own screenshot
flow. `false_success` and `fixed:false` here are harness artifacts, not model behavior.

Real-repo damage was **reverted to pristine** (`git checkout -- ThemeManager.swift
MiniPlayerView.swift`); the two edits were competent (the 0.72 bump and the token
unification are both defensible) — just applied to the wrong target.

## The methodology lesson (the real finding)

**File-injected sandboxing does not contain a builder that brings its own MCP tooling.**
When the agent has `xcodebuildmcp` (or any build tool that resolves by scheme/project
name rather than cwd), dropping scripts into a repo copy and setting `cwd` is not
hermetic — scheme resolution and absolute-path exploration both escape it. The isolation
must be enforced **below** the agent's tooling:

- **strip the competing MCP** for the run (force `./tools/run_app.sh` on the sandbox as
  the only build path — also forces the arms to actually differ), or
- **make the sandbox the resolved target** (a git worktree whose `Aria.xcodeproj` is the
  one the scheme opens, with no other copy on disk), or
- **filesystem isolation** (container/VM) so absolute paths can't reach the real repo.

And regardless: **assert the real repo is unchanged after every run, and fail loudly if
not** — a hermeticity check the harness lacked and now needs.

## One sub-finding survives the contamination

i1-A *did* fix its sandbox copy (grader-confirmed) — but from the **code tell**: `0.12`
is a visible copy of `cardSurface`, and the doc comment still read `0.62`. Same
code-transparent mechanism that solved the entire web round. So even this "hard" iOS bug
carried a code fingerprint and was not cleanly perception-load-bearing. A valid future
round needs bugs with **no** such fingerprint (e.g. a runtime-only stacking/opacity
result whose source reads as correct) on a **hermetic** harness.

## Status

P2's substantive conclusion is unchanged and rests on the **web round**: on
transparent/fast-loop regimes the arms don't separate, and the collateral findings
(phrasing-sensitive tier-0 verdicts; no self-escalation; verify-gate-as-outage; the
Max-plan oracle mechanics) are the value. The iOS falsification regime remains **untested**
pending a hermetic harness. Cost of this round: opencode-go builder time + ~1hr of
simulator builds; zero dollars; real-repo damage contained.
