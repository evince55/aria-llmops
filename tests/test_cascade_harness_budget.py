"""The perception-cascade harness has two spend paths and neither was named.

Correcting the framing I filed this under: the `claude -p` oracle was ALREADY
capped — the generated `diagnose.sh` counts `esc*-oracle.json` files and refuses
past three. The gaps are different and smaller than "unguarded":

1. That `3` is a **magic number**. Nobody can set it per run and no output
   records what it was, which is findings 12/19/23 exactly — a limit nobody
   chose and nothing reported.
2. Exhaustion **exits 0 with a message**. The S8 work established that a guard
   which ends quietly is indistinguishable from one that never fired.
3. The **builder** `opencode` call has no cap at all, only a wall-clock timeout.
   A timeout bounds how long a call runs, not how much it costs.

The oracle cannot be guarded from Python: `claude -p` runs inside a shell script
that the BUILDER invokes as a tool, so the harness never sees those calls. The
cap has to live in the script — which is why it is a call count, not a dollar
figure, and why that is the right unit anyway: `claude -p` is plan-covered, so
pricing it per token would invent a number the guard is built to refuse.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
HARNESS = Path(__file__).resolve().parents[1] / "experiments/perception_cascade/harness"
SCRIPTS = ("run_cell.py", "run_cell_ios.py")


def _src(name):
    return (HARNESS / name).read_text()


@pytest.mark.parametrize("name", SCRIPTS)
class TestOracleCapIsNamedNotMagic:
    def test_the_cap_is_no_longer_hardcoded(self, name):
        # `-ge 3` inline means the limit cannot be set and was never decided.
        assert '-ge 3' not in _src(name), "oracle cap is still a magic number"

    def test_the_cap_is_templated_from_a_variable(self, name):
        assert "{oracle_calls}" in _src(name)

    def test_the_script_exposes_a_flag_for_it(self, name):
        assert "--oracle-calls" in _src(name)


@pytest.mark.parametrize("name", SCRIPTS)
class TestBuilderCallIsGuarded:
    def test_it_offers_a_budget(self, name):
        assert "add_budget_args" in _src(name)

    def test_a_guard_is_actually_built(self, name):
        assert "guard_from_args" in _src(name)

    def test_the_builder_call_is_charged_before_it_runs(self, name):
        src = _src(name)
        charge = src.index("guard.charge") if "guard.charge" in src else -1
        call = src.index('"opencode", "run"')
        assert 0 < charge < call, "the builder must be charged BEFORE the subprocess"


@pytest.mark.parametrize("name", SCRIPTS)
class TestTheRunRecordsItsLimits:
    def test_the_summary_carries_the_oracle_cap(self, name):
        # A run that does not record its own limit cannot be compared to another.
        assert '"oracle_calls_cap": a.oracle_calls' in _src(name)

    def test_the_summary_carries_the_builder_guard_report(self, name):
        # None when unguarded is the honest value — it says "no limit was set"
        # rather than omitting the field and leaving the reader to assume one.
        assert '"builder_guard": (guard.report() if guard else None)' in _src(name)
