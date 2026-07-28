"""Every script that spends real money must expose --budget-usd, and be runnable.

Two failures this file exists to prevent, both of which actually happened:

1. **A flag that is not wired.** Five scripts spend through two paid-call
   primitives; adding the flag to four of them protects the four you thought of.
   This asserts the set by SEARCHING for spenders rather than listing them, so a
   new spender fails the test instead of being silently unguarded.

2. **A script that cannot run at all.** The guard shipped in the previous change
   placed `from telemetry.spend_guard import ...` ABOVE the `sys.path.insert`
   that makes `telemetry` importable. Every test still passed — pytest puts the
   repo root on `sys.path` — while `python evals/judge_labels.py --help` raised
   ModuleNotFoundError. The CLI of the exact script the breaker was built to
   protect was broken, and the suite could not see it.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Scripts whose CLI drives paid calls. Derived from a search, then pinned here so
# the test fails loudly if the set changes rather than quietly covering less.
PAID_SCRIPTS = (
    "evals/judge_labels.py",
    "evals/grade_ab.py",
    "evals/spec_probe.py",
    "evals/label_eval_set.py",
    "evals/distill_generate.py",
    "evals/regen_complex.py",
    # The cascade harnesses spend two ways: a priceable `opencode` builder
    # (guarded by --budget-usd) and a plan-covered `claude -p` oracle inside a
    # shell script the builder invokes (capped by --oracle-calls, because
    # pricing a plan-covered call per token would invent a number).
    "experiments/perception_cascade/harness/run_cell.py",
    "experiments/perception_cascade/harness/run_cell_ios.py",
)


def _help(script):
    return subprocess.run([sys.executable, str(REPO / script), "--help"],
                          capture_output=True, text=True, cwd=str(REPO))


@pytest.mark.parametrize("script", PAID_SCRIPTS)
def test_the_cli_actually_runs(script):
    # An import placed above sys.path.insert breaks this while tests stay green.
    r = _help(script)
    assert r.returncode == 0, f"{script} --help failed:\n{r.stderr[-600:]}"


@pytest.mark.parametrize("script", PAID_SCRIPTS)
def test_it_offers_a_budget(script):
    assert "--budget-usd" in _help(script).stdout, f"{script} can spend without a cap"


@pytest.mark.parametrize("script", PAID_SCRIPTS)
def test_the_help_admits_what_omitting_it_means(script):
    out = _help(script).stdout.lower()
    assert "unguarded" in out or "no limit" in out


def test_the_pinned_set_still_matches_what_actually_spends():
    """A new script that calls opencode must be added here, not discovered later.

    Greps for the paid-call primitives rather than the word "opencode", which
    also appears in comments and model-name checks.
    """
    spenders = set()
    candidates = list((REPO / "evals").glob("*.py"))
    candidates += list((REPO / "experiments").rglob("*.py"))
    for path in candidates:
        text = path.read_text()
        calls_primitive = ("call_judge(" in text or "judge_rows(" in text
                           or "make_teacher(" in text)
        defines_primitive = '["opencode"' in text or "'opencode'," in text
        has_cli = "add_argument" in text
        if has_cli and (calls_primitive or defines_primitive):
            # as_posix(), not str(): on Windows str() yields backslashes and
            # nothing matches the forward-slash pinned list, so every guarded
            # script reads as unguarded. CI caught this; macOS/Linux could not.
            spenders.add(path.relative_to(REPO).as_posix())
    missing = spenders - set(PAID_SCRIPTS)
    assert not missing, f"unguarded paid scripts: {sorted(missing)}"


def test_paths_are_compared_posix_style():
    """Guards the guard: a separator mismatch made every script look unguarded.

    The first version compared `str(path.relative_to(REPO))`, which is
    backslash-separated on Windows. The pinned list is forward-slash, so on
    Windows the set difference contained everything and the test failed for a
    reason that had nothing to do with spending. A test that only passes on the
    author's OS is not protecting the other two.
    """
    assert all("\\" not in s for s in PAID_SCRIPTS)
    assert all(Path(s).parts[0] in ("evals", "experiments") for s in PAID_SCRIPTS)
