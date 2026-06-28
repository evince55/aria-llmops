# Task 10 Report: CLI wiring (eval/dashboard/report/suggest) + README

## What was done

### TDD Steps

**Step 1 — Write failing tests:**
Created `tests/test_cli_commands.py` with 4 tests exactly as specified in the brief:
- `test_report_runs`: seeds a temp ledger, calls `cli.main(["report", "--ledger", ...])`, checks JSON output has `usage_events == 1`
- `test_eval_all_runs`: calls `cli.main(["eval", "all"])`, checks JSON output has `classification` and `efficiency` keys
- `test_dashboard_cmd_writes`: seeds a temp ledger, calls dashboard subcommand with `--out`, checks file was written
- `test_suggest_runs`: calls `cli.main(["suggest"])`, checks JSON output has `mismatches` key

**Step 2 — Confirmed failure:**
```
FAILED tests/test_cli_commands.py::test_report_runs - SystemExit: 2
FAILED tests/test_cli_commands.py::test_eval_all_runs - SystemExit: 2
FAILED tests/test_cli_commands.py::test_dashboard_cmd_writes - SystemExit: 2
FAILED tests/test_cli_commands.py::test_suggest_runs - SystemExit: 2
4 failed in 0.32s
```
(argparse `invalid choice: 'report'` — subcommands not yet registered)

**Step 3 — Extended `telemetry.py`:**
Added four handler functions before `_cmd_ingest`:
- `_cmd_report`: reads ledger, counts usage/route_decision events, sums costs, prints JSON
- `_cmd_eval`: lazy-imports from `evals/router_classification_eval.py` and `evals/routing_efficiency_eval.py`, runs the selected eval(s), prints JSON (strips `rows` key from efficiency output)
- `_cmd_dashboard`: lazy-imports from `dashboard/generate.py`, calls `generate(ledger=, out=)`, prints path as JSON
- `_cmd_suggest`: lazy-imports `load_dataset` + `ModelRouter`, iterates labeled dataset, collects mismatches between predicted and expected tier, prints JSON

Registered all four subparsers inside `build_parser()` before `return p`, preserving the existing `ingest` subparser unchanged.

**Step 4 — Confirmed pass:**
```
4 passed in 0.20s
```

**Step 5 — Wrote README.md** at repo root covering: what the toolkit does, CLI usage examples for all 5 subcommands (`ingest`, `report`, `eval all`, `dashboard`, `suggest`), test invocation, and the SessionEnd hook.

**Step 5 — Full suite:**
```
28 passed in 0.21s
```
(24 pre-existing + 4 new = 28, zero regressions)

**Step 6 — Committed and pushed.**

## Files changed

| File | Action |
|------|--------|
| `telemetry.py` | Modified — added `_cmd_report`, `_cmd_eval`, `_cmd_dashboard`, `_cmd_suggest`; registered 4 subparsers in `build_parser()` |
| `tests/test_cli_commands.py` | Created — 4 TDD tests |
| `README.md` | Created |

## Test output (new tests)

```
tests/test_cli_commands.py::test_report_runs PASSED
tests/test_cli_commands.py::test_eval_all_runs PASSED
tests/test_cli_commands.py::test_dashboard_cmd_writes PASSED
tests/test_cli_commands.py::test_suggest_runs PASSED
4 passed in 0.20s
```

## Full suite output

```
............................
28 passed in 0.21s
```

## Push result

```
To https://github.com/chaitea321/aria-llmops.git
 * [new branch]      feat/telemetry-evals -> feat/telemetry-evals
branch 'feat/telemetry-evals' set up to track 'origin/feat/telemetry-evals'
```
PR creation link: https://github.com/chaitea321/aria-llmops/pull/new/feat/telemetry-evals

## Commit SHAs

Base of Task 10 work: `24cf0ba` (Task 9 dashboard commit)
Task 10 commit: `4d43c13`

## Deviations from brief

None. All four handlers and subparser registrations are verbatim from the brief. README matches the specified template exactly.

## Concerns

None. The `_cmd_eval` handler uses lazy imports inside the function body (as specified), so the module-level import surface stays minimal and stdlib-only. All runtime modules remain stdlib-only; pytest is dev-only.

---

# Final-Review Polish (appended)

## Edits applied

1. **`evals/router_classification_eval.py`**: removed unused `import os`; replaced `confusion.setdefault(actual, defaultdict(int))[predicted] += 1` with direct `confusion[actual][predicted] += 1` (safe: confusion pre-seeded for all TIERS).
2. **`evals/routing_efficiency_eval.py`**: changed loop guard from `if e.get("event") and e.get("event") != "usage": continue` to `if e.get("event") != "usage": continue` (explicit event-type filter).
3. **`telemetry.py` (`_cmd_report`)**: changed `e.get("event", "usage") == "usage"` to `e.get("event") == "usage"`.
4. **`dashboard/generate.py` (`build_html`)**: same `event` filter fix — `e.get("event", "usage") == "usage"` → `e.get("event") == "usage"`.
5. **`tests/test_dashboard.py`**: added `test_build_html_escapes_special_chars` proving HTML-special characters in model names are escaped (`&lt;script&gt;` present, `<script>alert(1)</script>` absent).
6. **`README.md`**: added comment after `dashboard` line noting `--out PATH` overrides the default output path.

## Full-suite result

```
29 passed in 0.21s
```
