# Domain Concepts

The mental model behind the routing, classification, and grading systems.

## Complexity tiers

Every task is classified into one of four tiers. They are checked in priority
order: CRITICAL → COMPLEX → SIMPLE → MODERATE. MODERATE is checked last because
its keywords are individually low-precision.

### SIMPLE
Trivial, well-scoped changes. **Keywords:** `function`, `test`, `docs?`,
`comment`, `rename`, `format`, `typo`, `fix typo`, `print`, `log`, `tweak`,
`bump`, `build error`, `xcodebuild`, `simulator`, `lint`.

- Single keyword fires confidently
- TIER_PREFERENCE: `[llama-cpp/qwen35b]` (local only)

### MODERATE
Feature work, component building, wiring across files. **Keywords:** `feature`,
`component`, `implement`, `add`, `build`, `create`, `endpoint`, `view`,
`screen`, `connect`, `wire up`, `service`, `manager`, `extract`, `promote`,
`migrate`.

- Requires **≥2 distinct keyword hits** before being trusted
- Single hit → explicit default (low confidence, `matched=False`)
- Domain nouns (SwiftUI, @Published, controller) were *removed* — they signal
  domain, not difficulty
- TIER_PREFERENCE: `[llama-cpp/qwen35b, opencode/deepseek-v4-flash]`

### COMPLEX
Refactoring, concurrency, performance, subtle bugs, algorithm design.
**Keywords:** `algorithm`, `refactor`, `optimise/optimize`, `performance`,
`integrate`, `debug...root`, `root cause`, `concurrent`, `race condition`,
`memory leak`, and iOS/Swift concurrency terms (`async/await`, `actor
isolation`, `Sendable`, `@MainActor`, `AVPlayer`, `AVAudioEngine`,
`CoreAudio`, `MIDI`, etc.).

- Single keyword fires confidently
- TIER_PREFERENCE: `[llama-cpp/qwen35b, opencode-go/minimax-m3]`

### CRITICAL
Getting it wrong causes real harm. **Two categories of keywords:**

1. **Domain-signal keywords** — architecture, security, auth, encryption,
   credentials, JWT, OAuth, CSRF, XSS, migration, breaking, vulnerability,
   iOS/Apple security surfaces (Info.plist, ATS, entitlements, Keychain)

2. **Consequence-signal keywords** — high-precision phrasings that catch
   severe *outcomes* regardless of domain: `permanently los*`, `data loss`,
   `data corrupt`, `double charg*`, `leak* money`, `world-readable`,
   `data breach`, `account takeover`, `plaintext...password`, `production
   down/outage`

- Single keyword fires confidently
- TIER_PREFERENCE: `[opencode-go/minimax-m3, llama-cpp/qwen35b]` — CRITICAL
  prefers cloud first, but has a local fallback so the cost gate can downgrade

**Source:** `llmops.py` `COMPLEXITY_KEYWORDS` (lines 77–140), `TIER_PREFERENCE`
(lines 66–75)

## The hybrid classifier: keyword-first + 9B-rescue

The production routing strategy (`classify_hybrid`) is:

1. **Keyword-first:** run `classify_detailed()`. If any high-precision tier
   fires, or ≥2 MODERATE hits fire, trust it immediately — the keyword
   classifier is nearly perfect on the patterns it was tuned for.

2. **9B-rescue:** if keyword *defaulted* (`matched=False` — novel prose the
   keywords don't cover), consult the cheap 9B classifier model (9b-mythos)
   via `classify_via_model()`. The 9B reads intent from long, ambiguous
   prompts far better than keyword matching.

3. **Fallback:** if the 9B is unreachable or returns unparseable output,
   degrade to the keyword default (MODERATE).

**Why keyword-first, not 9B-primary:** The 9B has stable, reproducible
under-provisioning blind spots — e.g., it classifies audio-engine integration
as MODERATE and crash/race severity as COMPLEX instead of CRITICAL. The keyword
severity rules catch these. Both classifiers fail on *disjoint* inputs, so the
hybrid is strictly safer than either alone.

Measured on 42 labeled tasks: keyword=71.4%, 9B-primary=81.0%, **hybrid=83.3%**
— the hybrid is ≥ both parents on every dataset.

**Source:** `llmops.py` `classify_hybrid()` (lines 696–728), `ModelClassifier`
(lines 301–327)

## Consequence-based CRITICAL severity

CRITICAL is not about *domain vocabulary* but about *what happens if you get it
wrong*. The keyword list includes consequence-signal phrases (`permanently
loss`, `data corruption`, `double-charging`, `account takeover`) that fire
regardless of how innocuous the task sounds. A task like "a save race truncates
the file and users lose their data" or "downloaded files are world-readable to
other apps" is CRITICAL even though its vocabulary might otherwise suggest
MODERATE.

A mere mention of a sensitive domain is *not* enough — adding a payment button,
renaming an AuthManager, or editing encryption docs is NOT critical. The
phrasings are kept tight on purpose, verified against the labeled severity
near-miss set (0 false positives).

**Source:** `llmops.py` lines 86–105

## Outcome grading

Session outcomes come from user *reactions* to the assistant's completed work —
not from the task framing. Two-stage:

### Stage 1: Keyword heuristic (high precision)
`outcome_from_user_texts()` scans user turns for decisive phrases. Success
phrases: `works now`, `lgtm`, `looks good`, `ship it`, `merge it`, `fixed
now`, etc. Failure phrases: `still broken`, `doesn't work`, `not working`,
`regression`, `wrong output`, etc.

- **Negation guard:** success phrases preceded by a negator (`don't merge`,
  `not perfect`, `never merge to main`) are ignored
- **Last decisive signal wins:** an early complaint that's fixed later = success
- Returns `"success"`, `"failure"`, or `None` (no confident signal — we
  deliberately do NOT guess)

### Stage 2: Model grader (raises recall)
If keyword is inconclusive AND a `complete` callable is provided (the 9B
classifier model), the model reads the user's reaction turns holistically. The
prompt explicitly instructs: "FAILURE requires explicit dissatisfaction,"
"neutral approvals are SUCCESS or UNCLEAR, never FAILURE." Returns `None` on
UNCLEAR or error.

**Source:** `telemetry/outcomes.py`

## Rolling cost windows

CostMonitor gates cloud routing on *rolling* spend windows, not lifetime
totals. Each window (5h, 7d, 30d) is measured independently via
`CodingMemory.spend_since(seconds)`. Old spend ages out — once a task falls
outside a window, it no longer pressures the gate. This means the gate
reflects *recent* budget pressure rather than permanently forcing local routing
after cumulative spend crosses a cap.

Caps default to $12 (5h), $30 (weekly), $60 (monthly), with an 80% threshold.
Budget pressure is reported in `CostMonitor.generate_report()` as
`tier_utilization` percentages.

**Source:** `llmops.py` `CostMonitor` (lines 527–616)
