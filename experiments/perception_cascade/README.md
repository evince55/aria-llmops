# P2 — the perception cascade experiment

Can a cheap builder model (opencode-go/minimax-m3) fix **rendering** bugs it
cannot reliably see, if the harness gives it structured ways to look?

Motivating observation (owner, 2026-07-19): minimax screenshotted a broken UI
and still misread it, missed broken parts, and claimed success — *capture is
not comprehension*. The fix under test is a two-tier perception cascade
mirroring the project's text-routing cascade:

- **tier 0 (free, high-frequency):** Qwen2.5-VL-3B locally via mlx-vlm answers
  strict YES/NO questions about a fresh screenshot — verification, not diagnosis.
- **tier 1 (flat-rate, sparse):** `claude -p` headless on the Claude Max plan
  (read-only: `--allowedTools Read Glob Grep`, 3-call budget) — full root-cause
  diagnosis from screenshot + console + code, relayed to the builder verbatim.

Three arms (identical prompt + builder; only ./tools differ): A = screenshot
only; B = + tier-0 verify.sh; C = + tier-1 diagnose.sh. Three planted
single-token bugs in a copy of the real career-portal site (invisible tagline
via CSS token typo; negative-margin overlap; renamed JS export killing a module
with only console evidence). Grading is deterministic Playwright assertions on
the rendered page (`grade.js`) — never the builder's claims; `false_success` =
claimed DONE while the grader fails.

Run one cell:
    .venv/bin/python harness/run_cell.py --bug b1 --arm C --port 8611 \
        --base <pristine-clone> --out <cell-dir>

Results: `docs/research/` (perception-cascade results doc, written per run).
